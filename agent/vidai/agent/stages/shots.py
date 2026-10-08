"""Stage I — Shot Planning & Clip Segmentation: rules as constraints, agent writes the prompts (PRD §13)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError, call_module
from vidai.plan import Character, Shot, SpeechVisualBeat

PRODUCT_TYPES = {"hold_product", "wear_or_use_product", "product_close_up", "simple_demo"}


class _ShotPrompt(BaseModel):
    shotId: str
    prompt: str = ""
    framing: str = ""
    notes: str = ""


class _ShotReply(BaseModel):
    shots: list[_ShotPrompt]


def segment_beats(beats: list[SpeechVisualBeat], max_shot_s: float, min_shot_s: float,
                  hard_cap_s: float) -> tuple[list[Shot], list[str]]:
    """Hard constraints (PRD §13.4): never cut inside a beat; each clip within the model's stable
    duration; product visibility carried from the visual script; cuts between visual types."""
    shots: list[Shot] = []
    warnings: list[str] = []
    for beat in beats:
        duration = max(beat.estimatedDurationSec, 0.5)
        product = beat.visual.productRequired or beat.visual.type in PRODUCT_TYPES
        current = shots[-1] if shots else None
        mergeable = (
            current is not None
            and current.visualType == beat.visual.type
            and current.productVisible == product
            and current.durationSec + duration <= max_shot_s
        )
        if mergeable:
            current.beatIds.append(beat.beatId)
            current.durationSec = round(current.durationSec + duration, 2)
            current.speech = (current.speech + " " + beat.speech).strip()
            if beat.visual.action:
                current.continuityAnchors.setdefault("actions", "")
                current.continuityAnchors["actions"] = (current.continuityAnchors["actions"] + " / " + beat.visual.action).strip(" /")
            continue
        if duration > max_shot_s:
            warnings.append(f"beat {beat.beatId} speaks for {duration:.1f}s > {max_shot_s:.0f}s clip limit; "
                            f"clip capped at {hard_cap_s:.0f}s")
        shots.append(Shot(
            shotId=f"shot_{len(shots) + 1:02d}", title=beat.purpose or beat.beatId, beatIds=[beat.beatId],
            durationSec=round(min(max(duration, min_shot_s), hard_cap_s), 2), visualType=beat.visual.type,
            productVisible=product, framing=beat.visual.framing or "medium", speech=beat.speech,
            continuityAnchors={"actions": beat.visual.action} if beat.visual.action else {},
        ))
    return shots, warnings


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
    pronoun = "She" if character and character.presentation.startswith("fem") else ("He" if character and character.presentation.startswith("masc") else "They")
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
            f"anatomy, product label sharp and legible, no text overlays, no captions.")


class ShotPlannerStage(Stage):
    name = "shot_planner"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        settings = plan.generationSettings
        if not plan.speechVisualBeats:
            raise StageError("no script to plan shots from")
        shots, warnings = segment_beats(plan.speechVisualBeats, settings.maxShotSec, settings.minShotSec,
                                        ctx.settings.hard_max_shot_s)
        plan.warnings.extend(warnings)
        character = plan.character
        product_name = plan.product.productName if plan.product else "the product"
        base_anchors = dict(character.continuityAnchors) if character else {}
        for shot in shots:
            actions = shot.continuityAnchors.get("actions", "")
            shot.continuityAnchors = {**base_anchors, **({"actions": actions} if actions else {})}
            shot.prompt = template_prompt(shot, character, product_name)
        try:
            reply = await call_module(
                ctx, "shot_planner",
                {"product": plan.product, "character": character,
                 "shots": [{"shotId": s.shotId, "beatIds": s.beatIds, "durationSec": s.durationSec,
                            "visualType": s.visualType, "productVisible": s.productVisible,
                            "framing": s.framing, "speech": s.speech,
                            "actions": s.continuityAnchors.get("actions", "")} for s in shots],
                 "aspectRatio": settings.aspectRatio},
                model=_ShotReply,
            )
            by_id = {s.shotId: s for s in shots}
            for item in reply.shots:
                shot = by_id.get(item.shotId)
                if shot is None or not item.prompt.strip():
                    continue
                shot.prompt = item.prompt.strip()
                if item.framing:
                    shot.framing = item.framing
                if item.notes:
                    shot.repairHistory.append(f"planner: {item.notes}")
        except StageError as exc:   # template prompts are an acceptable fallback
            plan.warnings.append(f"shot prompt writer failed, using template prompts ({exc})")
        plan.shotPlan = shots
