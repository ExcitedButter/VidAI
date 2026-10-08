"""Stage I — Shot Planning & Clip Segmentation (PRD §13).

"Agent freedom, rules as constraints": the planner LLM proposes which consecutive beats share a
clip and writes each shot's generation prompt; deterministic rules reject anything that cuts
inside a beat, exceeds the model's stable clip length or drops a required product moment, and
fall back to a rule-based segmentation.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError, call_module
from vidai.plan import Character, Shot, SpeechVisualBeat, normalize_visual_type

PRODUCT_TYPES = {"hold_product", "wear_or_use_product", "product_close_up", "simple_demo"}
_TYPE_PRIORITY = ("simple_demo", "wear_or_use_product", "product_close_up", "hold_product",
                  "lifestyle_talking_head", "talking_head")


class _ShotProposal(BaseModel):
    shotId: str = ""
    beatIds: list[str] = Field(default_factory=list)
    visualType: str = ""
    framing: str = ""
    prompt: str = ""
    notes: str = ""


class _ShotReply(BaseModel):
    shots: list[_ShotProposal]


def _needs_product(beat: SpeechVisualBeat) -> bool:
    return beat.visual.productRequired or beat.visual.type in PRODUCT_TYPES


def _speech(group: list[SpeechVisualBeat]) -> float:
    return sum(max(b.estimatedDurationSec, 0.5) for b in group)


def _dominant_type(types: list[str]) -> str:
    for candidate in _TYPE_PRIORITY:
        if candidate in types:
            return candidate
    return "talking_head"


def build_shots(groups: list[list[SpeechVisualBeat]], min_shot_s: float, hard_cap_s: float) -> list[Shot]:
    shots: list[Shot] = []
    for index, group in enumerate(groups, start=1):
        speech_s = round(_speech(group), 2)
        actions = " / ".join(b.visual.action for b in group if b.visual.action)
        product_beat = next((b for b in group if _needs_product(b)), None)
        shots.append(Shot(
            shotId=f"shot_{index:02d}", title=group[0].purpose or group[0].beatId,
            beatIds=[b.beatId for b in group],
            durationSec=round(min(max(speech_s, min_shot_s), hard_cap_s), 2), speechSec=speech_s,
            visualType=_dominant_type([b.visual.type for b in group]),
            productVisible=product_beat is not None,
            framing=(product_beat or group[0]).visual.framing or "medium",
            speech=" ".join(b.speech for b in group).strip(),
            continuityAnchors={"actions": actions} if actions else {},
        ))
    return shots


def segment_beats(beats: list[SpeechVisualBeat], max_shot_s: float, min_shot_s: float,
                  hard_cap_s: float) -> tuple[list[Shot], list[str]]:
    """Rule-based segmentation (also the fallback when the planner's proposal breaks a constraint)."""
    groups: list[list[SpeechVisualBeat]] = []
    warnings: list[str] = []
    for beat in beats:
        duration = max(beat.estimatedDurationSec, 0.5)
        current = groups[-1] if groups else None
        if current is not None and _speech(current) + duration <= max_shot_s:
            same_kind = (current[-1].visual.type == beat.visual.type
                         and _needs_product(current[-1]) == _needs_product(beat))
            # merge across visual types only when the current shot is too short to stand alone
            if same_kind or _speech(current) < min_shot_s:
                current.append(beat)
                continue
        if duration > max_shot_s:
            warnings.append(f"beat {beat.beatId} speaks for {duration:.1f}s > {max_shot_s:.0f}s clip limit; "
                            f"clip capped at {hard_cap_s:.0f}s")
        groups.append([beat])
    if len(groups) >= 2 and _speech(groups[-1]) < min_shot_s and _speech(groups[-2]) + _speech(groups[-1]) <= max_shot_s:
        groups[-2].extend(groups.pop())   # a dangling short tail joins the previous shot
    return build_shots(groups, min_shot_s, hard_cap_s), warnings


def groups_from_proposal(proposal: list[_ShotProposal], beats: list[SpeechVisualBeat],
                         max_shot_s: float) -> tuple[Optional[list[list[SpeechVisualBeat]]], str]:
    """Validate the planner's grouping against the hard constraints (PRD §13.4)."""
    by_id = {b.beatId: b for b in beats}
    order = [b.beatId for b in beats]
    flat: list[str] = []
    groups: list[list[SpeechVisualBeat]] = []
    for item in proposal:
        if not item.beatIds:
            return None, f"{item.shotId or 'a shot'} has no beats"
        unknown = [bid for bid in item.beatIds if bid not in by_id]
        if unknown:
            return None, f"unknown beat ids {unknown}"
        group = [by_id[bid] for bid in item.beatIds]
        if _speech(group) > max_shot_s + 0.05:
            return None, f"{item.shotId or 'a shot'} speaks for {_speech(group):.1f}s > {max_shot_s:.0f}s"
        flat += item.beatIds
        groups.append(group)
    if flat != order:
        return None, "beats are not covered exactly once in order (a beat was split, dropped, duplicated or reordered)"
    return groups, ""


def anchor_sentence(character: Character | None) -> str:
    if character is None:
        return "A real creator films a selfie-style talking-head video"
    anchors = character.continuityAnchors
    parts = [f"{character.name.split(' - ')[0]}, {character.ageRange}, {character.presentation}".strip(", "),
             anchors.get("face", ""), anchors.get("hair", ""), f"wearing {anchors.get('wardrobe', character.wardrobe)}",
             anchors.get("accessories", ""), f"in {anchors.get('environment', character.environment)}",
             anchors.get("lighting", "")]
    return ", ".join(p for p in parts if p)


def template_prompt(shot: Shot, character: Character | None, product_name: str) -> str:
    presentation = (character.presentation if character else "").lower()
    pronoun = "She" if presentation.startswith("fem") else ("He" if presentation.startswith("masc") else "They")
    action = shot.continuityAnchors.get("actions") or {
        "talking_head": "talks straight to the camera",
        "hold_product": f"holds the {product_name} up to the camera and shows it",
        "wear_or_use_product": f"uses the {product_name} naturally while talking",
        "product_close_up": f"brings the {product_name} close to the camera, label sharp",
        "simple_demo": f"shows one simple use of the {product_name}",
        "lifestyle_talking_head": f"talks to the camera with the {product_name} in frame",
    }.get(shot.visualType, "talks to the camera")
    return (f"{anchor_sentence(character)}. {shot.framing} shot, single handheld phone camera, creator energy. "
            f"{pronoun} {action}. {pronoun} says: \"{shot.speech}\". Photoreal, natural skin, correct hand "
            f"anatomy, product label sharp and legible, lips move with the words, no text overlays, no captions.")


class ShotPlannerStage(Stage):
    name = "shot_planner"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        settings = plan.generationSettings
        beats = plan.speechVisualBeats
        if not beats:
            raise StageError("no script to plan shots from")
        hard_cap = ctx.settings.hard_max_shot_s
        fallback, warnings = segment_beats(beats, settings.maxShotSec, settings.minShotSec, hard_cap)
        plan.warnings.extend(warnings)
        character = plan.character
        product_name = plan.product.productName if plan.product else "the product"
        shots = fallback
        proposals: dict[tuple[str, ...], _ShotProposal] = {}
        try:
            reply = await call_module(
                ctx, "shot_planner",
                {"product": plan.product, "character": character,
                 "beats": [{"beatId": b.beatId, "purpose": b.purpose, "speech": b.speech,
                            "estimatedDurationSec": b.estimatedDurationSec, "visual": b.visual} for b in beats],
                 "constraints": {"maxShotSec": settings.maxShotSec, "minShotSec": settings.minShotSec,
                                 "allowJumpCuts": settings.allowJumpCuts, "aspectRatio": settings.aspectRatio},
                 "proposedShots": [{"shotId": s.shotId, "beatIds": s.beatIds, "speechSec": s.speechSec,
                                    "visualType": s.visualType, "productVisible": s.productVisible,
                                    "framing": s.framing, "speech": s.speech,
                                    "actions": s.continuityAnchors.get("actions", "")} for s in fallback]},
                model=_ShotReply,
            )
            groups, problem = groups_from_proposal(reply.shots, beats, settings.maxShotSec)
            if groups is None:
                plan.warnings.append(f"shot planner grouping rejected ({problem}); using rule-based segmentation")
            else:
                shots = build_shots(groups, settings.minShotSec, hard_cap)
            proposals = {tuple(item.beatIds): item for item in reply.shots}
        except StageError as exc:   # template prompts over the rule segmentation are an acceptable fallback
            plan.warnings.append(f"shot planner failed, using rule segmentation + template prompts ({exc})")

        base_anchors = dict(character.continuityAnchors) if character else {}
        for shot in shots:
            actions = shot.continuityAnchors.get("actions", "")
            shot.continuityAnchors = {**base_anchors, **({"actions": actions} if actions else {})}
            item = proposals.get(tuple(shot.beatIds))
            if item is not None:
                if item.visualType:
                    wanted = normalize_visual_type(item.visualType)
                    if not shot.productVisible or wanted in PRODUCT_TYPES:   # product moments stay product moments
                        shot.visualType = wanted
                if item.framing:
                    shot.framing = item.framing
                if item.notes:
                    shot.repairHistory.append(f"planner: {item.notes}")
            shot.prompt = (item.prompt.strip() if item and item.prompt.strip() else template_prompt(shot, character, product_name))
        plan.shotPlan = shots
