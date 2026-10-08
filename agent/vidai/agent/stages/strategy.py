"""Stages B, C, D — Audience Intelligence, Selling Angle Engine, Script Strategy Router."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError, call_module
from vidai.plan import ANGLE_FAMILIES, ARCHETYPES, Audience, ScriptStrategy, SellingAngle

SCORE_KEYS = ("audienceRelevance", "emotionalStrength", "differentiation",
              "visualPotential", "talkingHeadSuitability", "hookPotential")


class _AudienceReply(BaseModel):
    audiences: list[Audience] = Field(min_length=1, max_length=5)
    recommendedIndex: int = 0


class _AngleReply(BaseModel):
    angles: list[SellingAngle] = Field(min_length=2, max_length=8)
    recommendedIndex: int = 0


def _bounded(index: Any, length: int) -> int:
    try:
        index = int(index)
    except (TypeError, ValueError):
        return 0
    return index if 0 <= index < length else 0


class AudienceStage(Stage):
    name = "audience_analyst"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        reply = await call_module(
            ctx, "audience_analyst",
            {"product": plan.product, "durationSec": plan.generationSettings.targetDurationSec},
            model=_AudienceReply,
        )
        plan.audienceCandidates = reply.audiences[:3]            # PRD §5.4: 1-3 hypotheses
        plan.audienceIndex = _bounded(reply.recommendedIndex, len(plan.audienceCandidates))
        for audience in plan.audienceCandidates:
            if not audience.reason:
                audience.reason = f"{audience.evidence} from the product page"


class AngleStage(Stage):
    name = "angle_strategist"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        if plan.audience is None:
            raise StageError("no audience selected")
        reply = await call_module(
            ctx, "angle_strategist",
            {"product": plan.product, "audience": plan.audience,
             "durationSec": plan.generationSettings.targetDurationSec},
            model=_AngleReply,
        )
        angles = reply.angles
        for angle in angles:
            if angle.angleFamily not in ANGLE_FAMILIES:
                angle.angleFamily = "outcome"
            scores = [float(angle.scores.get(key, 0.0)) for key in SCORE_KEYS if key in angle.scores]
            angle.score = round(sum(scores) / len(scores), 3) if scores else angle.score
        recommended = angles[_bounded(reply.recommendedIndex, len(angles))]
        ranked = sorted(angles, key=lambda a: a.score, reverse=True)[:5]   # PRD §6.4: 3-5 ranked
        if recommended not in ranked:
            ranked.insert(0, recommended)
        plan.angleCandidates = ranked
        plan.angleIndex = ranked.index(recommended)


_FAMILY_TO_ARCHETYPE = {
    "pain_point": "pas_proof", "outcome": "pas_proof", "aspirational_lifestyle": "desired_life_bridge",
    "identity": "desired_life_bridge", "contrarian": "contrarian_reframe", "curiosity": "curiosity_reveal",
    "demo_first": "demo_first", "personal_discovery": "personal_discovery",
}


class ScriptRouterStage(Stage):
    """LLM / rule hybrid (PRD §7): the LLM picks, the rule table is the fallback."""

    name = "script_router"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        angle = plan.sellingAngle
        if angle is None:
            raise StageError("no selling angle selected")
        strategy = await call_module(
            ctx, "script_router",
            {"product": plan.product, "audience": plan.audience, "sellingAngle": angle,
             "character": plan.character, "durationSec": plan.generationSettings.targetDurationSec},
            model=ScriptStrategy,
        )
        if strategy.archetype not in ARCHETYPES:
            strategy.archetype = _FAMILY_TO_ARCHETYPE.get(angle.angleFamily, "pas_proof")
            strategy.reason = (strategy.reason + " (archetype corrected by rule table)").strip()
        if not strategy.coreMessage:
            strategy.coreMessage = angle.corePromise or angle.oneLineIdea
        plan.scriptStrategy = strategy
