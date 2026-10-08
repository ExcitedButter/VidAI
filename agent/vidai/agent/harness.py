"""CreativePipeline — the PRD §19 state machine that drives every module over one Creative Plan.

    DRAFT -> ANALYZING_PRODUCT -> STRATEGY_READY* -> CHARACTER_READY* -> SCRIPT_GENERATING
          -> SCRIPT_QC -> SCRIPT_READY* -> SHOT_PLANNING -> VIDEO_GENERATING -> VIDEO_QC
          -> (REPAIRING -> VIDEO_QC)* -> READY | FAILED            (* = guided-mode pause point)

Stages are grouped so that a user override (angle / character / script / single shot) restarts
the pipeline from the right group only — never a full upstream rollback (PRD §19.1).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Optional

from vidai.agent.run_context import PipelineContext, PipelineHalt
from vidai.agent.stages.base import Stage
from vidai.agent.stages.character import CharacterStage
from vidai.agent.stages.product_parser import ProductParserStage
from vidai.agent.stages.script import BeatPlannerStage, CopywriterStage, ScriptQCStage
from vidai.agent.stages.shots import ShotPlannerStage
from vidai.agent.stages.strategy import AngleStage, AudienceStage, ScriptRouterStage
from vidai.agent.stages.video import AssemblerStage, VideoStage
from vidai.plan import CreativePlan, SpeechVisualBeat, StageEvent, Status, utc_now_iso

logger = logging.getLogger(__name__)

GROUPS: tuple[str, ...] = ("product", "strategy", "character", "script", "script_qc", "shots", "video", "assemble")
PAUSE_POINTS: dict[str, Status] = {
    "strategy": Status.STRATEGY_READY,
    "character": Status.CHARACTER_READY,
    "script_qc": Status.SCRIPT_READY,
}
STAGE_GROUP: dict[str, str] = {
    "product_parser": "product", "audience_analyst": "strategy", "angle_strategist": "strategy",
    "character_matcher": "character", "script_router": "script", "beat_planner": "script",
    "copywriter": "script", "script_qc": "script_qc", "shot_planner": "shots", "video": "video",
    "assembler": "assemble",
}
_RESUME_FROM: dict[Status, Optional[str]] = {
    Status.DRAFT: "product", Status.ANALYZING_PRODUCT: "product",
    Status.STRATEGY_READY: "character", Status.CHARACTER_READY: "script",
    Status.SCRIPT_GENERATING: "script", Status.SCRIPT_QC: "script_qc", Status.SCRIPT_READY: "shots",
    Status.SHOT_PLANNING: "shots", Status.VIDEO_GENERATING: "video", Status.VIDEO_QC: "video",
    Status.REPAIRING: "video", Status.READY: None,
}


class CreativePipeline:
    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx

    # ------------------------------------------------------------------ driver
    async def run(self, start_group: str = "product", stop_after: Optional[str] = None) -> CreativePlan:
        if start_group not in GROUPS:
            raise ValueError(f"unknown group {start_group!r}; groups: {GROUPS}")
        ctx, plan = self.ctx, self.ctx.plan
        ctx.ensure_dirs()
        plan.error = None
        ctx.trace("pipeline_start", start_group=start_group, version=plan.version, mode=plan.mode)
        try:
            for group in GROUPS[GROUPS.index(start_group):]:
                await getattr(self, f"_run_{group}")()
                ctx.checkpoint(group)
                if self._should_pause(group):
                    try:
                        await ctx.pause(group)
                    except PipelineHalt:
                        ctx.trace("pipeline_halt", group=group)
                        ctx.checkpoint(f"{group}_halted")
                        return plan
                    await self._after_pause(group)
                if stop_after == group:
                    ctx.trace("pipeline_stop", group=group)
                    return plan
            ctx.trace("pipeline_end", final_video=plan.finalVideo, warnings=len(plan.warnings))
        except Exception as exc:  # any stage failure ends in FAILED with a readable reason
            logger.exception("pipeline failed while %s", plan.status.value)
            plan.status = Status.FAILED
            plan.error = f"{type(exc).__name__}: {exc}"
            ctx.trace("pipeline_failed", error=plan.error)
            ctx.checkpoint("failed")
        return plan

    def _should_pause(self, group: str) -> bool:
        plan = self.ctx.plan
        if plan.mode != "guided":
            return False
        if group in PAUSE_POINTS:
            return True
        # PRD §4.4: thin / ambiguous product data -> the user confirms before strategy is built on it
        return group == "product" and product_needs_confirmation(plan)

    async def _after_pause(self, group: str) -> None:
        """Apply what the user did at the pause point (PRD §12 Guided flow)."""
        plan = self.ctx.plan
        if group == "script_qc" and plan.userOverrides.pop("pendingScriptQC", False):
            plan.userOverrides["script"] = f"edited at v{plan.version}"
            await self._run_script_qc()
        self.ctx.checkpoint(f"{group}_confirmed")

    # ------------------------------------------------------------------ groups
    async def _run_product(self) -> None:
        self._set(Status.ANALYZING_PRODUCT)
        await self._stage(ProductParserStage())

    async def _run_strategy(self) -> None:
        self._set(Status.ANALYZING_PRODUCT)
        await self._stage(AudienceStage())
        await self._stage(AngleStage())
        self._reapply_override("angle", self.ctx.plan.select_angle)
        self._set(Status.STRATEGY_READY)

    async def _run_character(self) -> None:
        await self._stage(CharacterStage())
        self._reapply_override("character", self.ctx.plan.select_character)
        self._set(Status.CHARACTER_READY)

    async def _run_script(self) -> None:
        self._set(Status.SCRIPT_GENERATING)
        for stage in (ScriptRouterStage(), BeatPlannerStage(), CopywriterStage()):
            await self._stage(stage)

    async def _run_script_qc(self) -> None:
        self._set(Status.SCRIPT_QC)
        await self._stage(ScriptQCStage())
        self._set(Status.SCRIPT_READY)

    async def _run_shots(self) -> None:
        self._set(Status.SHOT_PLANNING)
        await self._stage(ShotPlannerStage())

    async def _run_video(self) -> None:
        await self._stage(VideoStage())   # moves through VIDEO_GENERATING / VIDEO_QC / REPAIRING itself

    async def _run_assemble(self) -> None:
        await self._stage(AssemblerStage())
        self._set(Status.READY)

    # ------------------------------------------------------------------ helpers
    def _set(self, status: Status) -> None:
        plan = self.ctx.plan
        if plan.status != status:
            self.ctx.trace("status", from_status=plan.status.value, to_status=status.value)
            plan.status = status

    def _reapply_override(self, key: str, selector: Any) -> None:
        """On re-runs, keep a selection the user made earlier if the candidate still exists."""
        wanted = self.ctx.plan.userOverrides.get(key)
        if not wanted:
            return
        try:
            selector(wanted, by_user=True)
        except KeyError:
            self.ctx.plan.warnings.append(f"earlier {key} override {wanted!r} no longer among candidates; using the recommendation")

    async def _stage(self, stage: Stage) -> None:
        ctx = self.ctx
        event = StageEvent(stage=stage.name, status=ctx.plan.status.value, startedAt=utc_now_iso())
        ctx.trace("stage_start", stage=stage.name)
        started = time.monotonic()
        try:
            await stage.run(ctx)
        except Exception as exc:
            event.ok, event.note, event.finishedAt = False, str(exc)[:300], utc_now_iso()
            ctx.plan.history.append(event)
            raise
        event.finishedAt = utc_now_iso()
        event.note = f"{time.monotonic() - started:.1f}s"
        ctx.plan.history.append(event)
        ctx.trace("stage_end", stage=stage.name, seconds=round(time.monotonic() - started, 2))
        ctx.checkpoint(stage.name)


def product_needs_confirmation(plan: CreativePlan) -> bool:
    product = plan.product
    return product is not None and (product.confidence < 0.5 or product.multipleProductsOnPage)


# ---------------------------------------------------------------------- resume / revise policy
def resume_group(plan: CreativePlan) -> Optional[str]:
    """Which group continues a saved plan; None when it is already READY."""
    if plan.status == Status.FAILED:
        for event in reversed(plan.history):
            if not event.ok:
                return STAGE_GROUP.get(event.stage, "product")
        return "product"
    return _RESUME_FROM[plan.status]


def prepare_revision(
    plan: CreativePlan,
    *,
    angle_id: Optional[str] = None,
    character_id: Optional[str] = None,
    script_beats: Optional[Iterable[Any]] = None,
    shot_ids: Optional[Iterable[str]] = None,
    regenerate_script: bool = False,
    regenerate_beats: Optional[Iterable[str]] = None,
) -> str:
    """Apply one user change, bump the version, and return the group to restart from.

    PRD §19.1: only the dependent downstream stages re-run; upstream results stay untouched.
    """
    plan.version += 1
    plan.error = None
    plan.finalVideo = None
    if angle_id:
        plan.select_angle(angle_id, by_user=True)
        if "character" in plan.userOverrides:   # user already picked a person: keep them
            _clear_from(plan, "script")
            return "script"
        _clear_from(plan, "character")
        return "character"
    if character_id:
        plan.select_character(character_id, by_user=True)
        _clear_from(plan, "script")
        return "script"
    if script_beats is not None:
        beats = [b if isinstance(b, SpeechVisualBeat) else SpeechVisualBeat.model_validate(b) for b in script_beats]
        if not beats:
            raise ValueError("edited script has no beats")
        plan.speechVisualBeats = beats
        plan.userOverrides["script"] = f"edited at v{plan.version}"
        _clear_from(plan, "shots")
        return "script_qc"
    if regenerate_script:   # PRD §18.4 "Regenerate all": same strategy + character, fresh beats and copy
        _clear_from(plan, "script")
        plan.userOverrides["regeneratedScript"] = f"v{plan.version}"
        return "script"
    if regenerate_beats:    # PRD §22 P1 "Beat-level regenerate": rewrite only those beats, then QC again
        wanted = list(dict.fromkeys(regenerate_beats))
        known = {b.beatId for b in plan.speechVisualBeats}
        missing = [b for b in wanted if b not in known]
        if missing:
            raise KeyError(f"unknown beat ids {missing}; beats: {sorted(known)}")
        plan.userOverrides["regenerateBeats"] = wanted
        _clear_from(plan, "shots")
        return "script_qc"
    if shot_ids:
        wanted = set(shot_ids)
        known = {s.shotId for s in plan.shotPlan}
        if not wanted <= known:
            raise KeyError(f"unknown shot ids {sorted(wanted - known)}; shots: {sorted(known)}")
        for shot in plan.shotPlan:
            if shot.shotId in wanted:
                shot.status = "planned"
                shot.qc = None
                shot.repairHistory.append(f"user requested regeneration at v{plan.version}")
        plan.userOverrides.setdefault("regeneratedShots", []).extend(sorted(wanted))
        plan.qc["video"] = None
        return "video"
    raise ValueError("nothing to revise: pass an angle, a character, an edited script, beats or shots to regenerate")


def _clear_from(plan: CreativePlan, group: str) -> None:
    order = ("character", "script", "shots", "video")
    index = order.index(group)
    if index <= 0:
        plan.characterCandidates, plan.characterIndex, plan.characterBrief = [], None, None
    if index <= 1:
        plan.scriptStrategy, plan.beats, plan.speechVisualBeats = None, [], []
        plan.qc["script"] = None
    if index <= 2:
        plan.shotPlan = []
        plan.qc["video"] = None
    plan.finalVideo = None
