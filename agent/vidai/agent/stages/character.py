"""Stage G — Character Casting: curated-library matching + optional new-character brief (PRD §10-11)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError, call_module
from vidai.plan import Character, CharacterBrief, CharacterScores, new_id

DEFAULT_LIBRARY = Path(__file__).resolve().parents[2] / "data" / "character_library.json"
MATCH_THRESHOLD = 0.45


class _RankedItem(BaseModel):
    characterId: str
    matchScore: float = 0.0
    whyThisPerson: str = ""


class _MatchReply(BaseModel):
    ranked: list[_RankedItem]
    recommendedIndex: int = 0


class _BriefReply(CharacterBrief):
    name: str = ""
    voice: str = ""
    continuityAnchors: dict[str, str] = {}


def load_library(path: Path | None, personal: Path | None = None) -> list[Character]:
    """Curated library plus the user's personal library (characters saved with `save-character`)."""
    library_path = path or DEFAULT_LIBRARY
    data = json.loads(library_path.read_text(encoding="utf-8"))
    characters = [Character.model_validate(item) for item in data.get("characters", [])]
    if personal and personal.is_file():
        extra = json.loads(personal.read_text(encoding="utf-8")).get("characters", [])
        characters += [Character.model_validate(item) for item in extra]
    return characters


def interaction_needs(product) -> list[str]:
    """PRD §11.3 matching input 'Product Interaction Needs', derived from the category (PRD §8.5)."""
    text = " ".join([product.category, product.positioning, " ".join(product.useCases[:3])]).lower() if product else ""
    if re.search(r"apparel|cloth|legging|shirt|shoe|sneaker|footwear|activewear|jacket|pant|dress|sock|hat|bag|wallet|watch|jewel|glass", text):
        return ["worn or carried on camera", "light movement that shows fit / use"]
    if re.search(r"skin|serum|cream|beauty|makeup|cosmetic|brow|lip|hair|fragrance|lotion", text):
        return ["held in hand with the label visible", "light application gesture"]
    if re.search(r"coffee|tea|drink|bottle|food|kitchen|cook|kettle|snack|supplement|brew", text):
        return ["held and used once (pour / sip / scoop)", "product close-up"]
    if re.search(r"tech|electronic|device|gadget|phone|headphone|speaker|camera|desk|keyboard|charger|smart", text):
        return ["held and shown to camera", "one simple on-screen demo"]
    return ["held and shown to camera when the core benefit is explained"]


def _tokens(*texts: Any) -> set[str]:
    out: set[str] = set()
    for text in texts:
        if isinstance(text, (list, tuple, set)):
            out |= _tokens(*text)
        elif isinstance(text, dict):
            out |= _tokens(*text.values())
        elif text:
            out |= {t for t in re.findall(r"[a-z]{3,}", str(text).lower())}
    return out


def axis_weights(angle_family: str | None, category: str) -> dict[str, float]:
    """PRD §10.3: weights follow the angle and category (beauty / lifestyle -> aspirational, ...)."""
    weights = {"relatable": 0.4, "aspirational": 0.4, "distinctive": 0.2}
    if angle_family in ("aspirational_lifestyle", "identity"):
        weights = {"relatable": 0.3, "aspirational": 0.5, "distinctive": 0.2}
    elif angle_family in ("curiosity", "contrarian"):
        weights = {"relatable": 0.3, "aspirational": 0.3, "distinctive": 0.4}
    elif angle_family in ("pain_point", "demo_first", "personal_discovery"):
        weights = {"relatable": 0.5, "aspirational": 0.3, "distinctive": 0.2}
    if re.search(r"beauty|skin|fashion|apparel|activewear|lifestyle|jewel", category or "", re.I):
        weights["aspirational"] = min(0.6, weights["aspirational"] + 0.1)
        weights["relatable"] = max(0.2, 1.0 - weights["aspirational"] - weights["distinctive"])
    return weights


def rule_score(character: Character, product, audience, weights: dict[str, float]) -> float:
    cat_tokens = _tokens(product.category, product.positioning, product.useCases[:3]) if product else set()
    cat_fit = 1.0 if cat_tokens & _tokens(character.bestForCategories) else 0.3
    aud_tokens = _tokens(audience.segmentName, audience.aspirationalIdentity, audience.demographics,
                         audience.behaviors[:3]) if audience else set()
    overlap = len(aud_tokens & _tokens(character.bestForAudiences, character.lifestyle))
    aud_fit = min(1.0, 0.3 + 0.25 * overlap)
    axes = character.scores
    axis = (weights["relatable"] * axes.relatability + weights["aspirational"] * axes.aspiration
            + weights["distinctive"] * axes.distinctiveness)
    return round(0.35 * cat_fit + 0.2 * aud_fit + 0.35 * axis + 0.1 * axes.trust, 3)


class CharacterStage(Stage):
    name = "character_matcher"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        product, audience, angle = plan.product, plan.audience, plan.sellingAngle
        if product is None or audience is None or angle is None:
            raise StageError("character casting needs product, audience and angle")
        weights = axis_weights(angle.angleFamily, product.category)
        needs = interaction_needs(product)
        library = load_library(ctx.settings.character_library, ctx.settings.data_dir / "personal_library.json")
        if not library:
            raise StageError("character library is empty")
        for character in library:
            character.matchScore = rule_score(character, product, audience, weights)
        candidates = sorted(library, key=lambda c: c.matchScore, reverse=True)[:3]   # PRD §11.3 top 3

        reply = await call_module(
            ctx, "character_matcher",
            {"product": product, "audience": audience, "sellingAngle": angle, "weights": weights,
             "productInteractionNeeds": needs, "candidates": candidates},
            model=_MatchReply,
        )
        by_id = {c.characterId: c for c in candidates}
        ranked: list[Character] = []
        for item in reply.ranked:
            character = by_id.get(item.characterId)
            if character is None:
                continue
            character.whyThisPerson = item.whyThisPerson or character.whyThisPerson
            if 0.0 <= item.matchScore <= 1.0:
                character.matchScore = round(0.5 * character.matchScore + 0.5 * item.matchScore, 3)
            ranked.append(character)
        ranked += [c for c in candidates if c not in ranked]
        recommended = ranked[reply.recommendedIndex] if 0 <= reply.recommendedIndex < len(ranked) else ranked[0]

        want_new = bool(plan.userOverrides.get("newCharacter")) or recommended.matchScore < MATCH_THRESHOLD
        if want_new:
            generated = await self._generate_character(ctx, weights, needs)
            ranked.insert(0, generated)
            recommended = generated if plan.userOverrides.get("newCharacter") else recommended
        plan.characterCandidates = ranked
        plan.characterIndex = ranked.index(recommended)
        ctx.save_json("character", "character_candidates.json",
                      {"recommendedIndex": plan.characterIndex, "weights": weights,
                       "productInteractionNeeds": needs, "candidates": ranked})

    async def _generate_character(self, ctx: PipelineContext, weights: dict[str, float],
                                  needs: list[str]) -> Character:
        """PRD §11.4: generation only from a structured Character Brief; anchors locked up front."""
        plan = ctx.plan
        brief = await call_module(
            ctx, "character_brief",
            {"product": plan.product, "audience": plan.audience, "sellingAngle": plan.sellingAngle,
             "weights": weights, "productInteractionNeeds": needs,
             "avoid": plan.product.constraints if plan.product else []},
            model=_BriefReply,
        )
        if not brief.productInteractionNeeds:
            brief.productInteractionNeeds = list(needs)
        plan.characterBrief = CharacterBrief.model_validate(brief.model_dump())
        ctx.save_json("character", "character_brief.json", brief)
        anchors = dict(brief.continuityAnchors) or {
            "face": brief.visualIdentity.get("face", ""), "hair": brief.visualIdentity.get("hair", ""),
            "wardrobe": brief.style.get("wardrobe", ""), "accessories": brief.style.get("accessories", ""),
            "environment": brief.environment, "lighting": "soft natural light", "voice": brief.voice,
        }
        return Character(
            characterId=new_id("gen"), name=brief.name or "Generated creator", source="generated",
            ageRange=brief.ageRange, presentation=brief.presentation, personality=brief.personality,
            lifestyle="; ".join(brief.lifestyleSignals), environment=brief.environment,
            wardrobe=brief.style.get("wardrobe", ""), voice=brief.voice,
            bestForCategories=brief.categoryFit, scores=CharacterScores(
                aspiration=brief.aspirationalWeight + 0.3, distinctiveness=brief.distinctiveWeight + 0.3,
                trust=0.6, relatability=brief.relatableWeight + 0.3),
            continuityAnchors=anchors, matchScore=0.6,
            whyThisPerson="Generated from a Character Brief tailored to this audience and angle "
                          "(no reference assets yet; identity is held by text anchors).",
        )
