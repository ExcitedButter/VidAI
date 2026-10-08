"""Stages J, K and the Assembler — generation with continuity, shot-level QC + repair, concat (PRD §14-15)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from vidai.agent.media import concat_clips, extract_frames, ffprobe_metadata, normalize_clip
from vidai.agent.prompts import load_prompt
from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError
from vidai.plan import QCCheck, Shot, ShotQC, Status, VideoQC

logger = logging.getLogger(__name__)

_SIMPLIFY_SUFFIX = (" Simplified action: the character simply holds the product up to the camera, "
                    "then brings it closer for a clear close-up while talking; no complex hand motion.")
_FRAMING_SUFFIX = " Use a slightly wider medium shot with the product and both hands fully in frame."
_ALT_TAKE_SUFFIX = " Alternative take: same scene and person, small natural variation in delivery."


def _first_frame(ctx: PipelineContext, shot: Shot) -> Path | None:
    """Continuity technique (PRD §14.3): reuse the character reference when one exists."""
    character = ctx.plan.character
    if character:
        for asset in character.visualReferenceAssets:
            if Path(asset).is_file():
                return Path(asset)
    return None


async def _generate_shot(ctx: PipelineContext, shot: Shot) -> None:
    settings = ctx.plan.generationSettings
    shot.attempts += 1
    out_path = ctx.clips_dir / f"{shot.shotId}_a{shot.attempts:02d}.mp4"
    last_error: Exception | None = None
    for attempt in range(3):   # network / API errors: back off, identical request (PRD §19.1)
        try:
            result = await ctx.seedance.generate_to_file(
                prompt=shot.prompt, out_path=out_path, first_frame_image=_first_frame(ctx, shot),
                duration_s=max(1, int(round(shot.durationSec))), ratio=settings.aspectRatio,
                resolution=settings.resolution,
            )
            shot.clipPath = result.video_path
            shot.status = "generated"
            ctx.trace("shot_generated", shot=shot.shotId, attempt=shot.attempts, clip=result.video_path)
            return
        except Exception as exc:
            last_error = exc
            ctx.trace("shot_error", shot=shot.shotId, attempt=shot.attempts, error=str(exc)[:300])
            await asyncio.sleep(min(2 ** attempt, 10))
    raise StageError(f"generation failed for {shot.shotId}: {last_error}")


async def _qc_shot(ctx: PipelineContext, shot: Shot, previous: Shot | None) -> ShotQC:
    plan = ctx.plan
    clip = Path(shot.clipPath or "")
    if not clip.is_file():
        return ShotQC(status="hard_fail", reason="clip missing", repairAction="retry_same_prompt")
    try:
        meta = ffprobe_metadata(clip)
    except Exception as exc:
        return ShotQC(status="hard_fail", reason=f"unreadable clip: {exc}", repairAction="retry_same_prompt")
    checks: dict[str, QCCheck] = {}
    if meta["duration_s"] < 0.5:
        return ShotQC(status="hard_fail", reason="empty clip", repairAction="retry_same_prompt")
    tolerance = max(1.0, 0.3 * shot.durationSec)
    if abs(meta["duration_s"] - shot.durationSec) > tolerance:
        checks["timing"] = QCCheck(status="warn", reason=f"clip {meta['duration_s']:.1f}s vs planned {shot.durationSec:.1f}s")
    else:
        checks["timing"] = QCCheck(status="pass", reason=f"{meta['duration_s']:.1f}s")
    frames = extract_frames(clip, ctx.frames_dir / clip.stem, 3, meta["duration_s"])
    references: list[str] = []
    if previous and previous.clipPath:
        references += sorted(str(p) for p in (ctx.frames_dir / Path(previous.clipPath).stem).glob("frame_*.jpg"))[:2]
    if plan.character:
        references += [a for a in plan.character.visualReferenceAssets if Path(a).is_file()][:1]
    context = {
        "shotId": shot.shotId, "visualType": shot.visualType, "productVisible": shot.productVisible,
        "speech": shot.speech, "product": plan.product.productName if plan.product else "",
        "continuityAnchors": shot.continuityAnchors, "candidateFrames": len(frames), "referenceFrames": len(references),
    }
    try:
        reply = await ctx.vision.vision_json(
            load_prompt("video_qc") + "\n\nContext:\n" + json.dumps(context, ensure_ascii=False),
            frames + references, purpose="video_qc",
        )
        qc = ShotQC.model_validate(reply)
    except Exception as exc:
        plan.warnings.append(f"{shot.shotId}: video QC judge unavailable ({str(exc)[:120]}); clip accepted on rules only")
        qc = ShotQC(status="pass", reason="judge unavailable; rule checks only")
    qc.checks = {**qc.checks, **checks}
    return qc


def _apply_repair(shot: Shot, qc: ShotQC) -> None:
    """Repair Agent policy (PRD §15.2): fix the failing shot, never redo the whole video."""
    action = qc.repairAction or "retry_same_prompt"
    shot.repairHistory.append(f"attempt {shot.attempts}: {action} ({qc.reason[:80]})")
    if shot.attempts >= 2 and action == "retry_same_prompt":
        action = "simplify_action"           # second failure: simplify instead of retrying blindly
    if action in ("simplify_action", "downgrade_visual_action"):
        if shot.visualType in ("wear_or_use_product", "simple_demo"):
            shot.visualType = "hold_product"
        if _SIMPLIFY_SUFFIX not in shot.prompt:
            shot.prompt += _SIMPLIFY_SUFFIX
    elif action == "change_framing":
        shot.framing = "medium"
        if _FRAMING_SUFFIX not in shot.prompt:
            shot.prompt += _FRAMING_SUFFIX
    elif action == "replace_clip":
        if _ALT_TAKE_SUFFIX not in shot.prompt:
            shot.prompt += _ALT_TAKE_SUFFIX
    shot.status = "planned"


class VideoStage(Stage):
    """VIDEO_GENERATING -> VIDEO_QC -> (REPAIRING -> VIDEO_QC)* for failed shots only."""

    name = "video"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        settings = plan.generationSettings
        if not plan.shotPlan:
            raise StageError("no shot plan")
        pending = [s for s in plan.shotPlan if s.status in ("planned", "failed")]
        video_qc = VideoQC()
        for round_index in range(settings.maxVideoRepairs + 1):
            plan.status = Status.VIDEO_GENERATING if round_index == 0 else Status.REPAIRING
            for shot in pending:
                await _generate_shot(ctx, shot)
            ctx.checkpoint(f"video_gen_r{round_index}")
            plan.status = Status.VIDEO_QC
            failed: list[Shot] = []
            for index, shot in enumerate(plan.shotPlan):
                if shot not in pending:
                    continue
                previous = plan.shotPlan[index - 1] if index > 0 else None
                qc = await _qc_shot(ctx, shot, previous)
                shot.qc = qc
                video_qc.shots[shot.shotId] = qc
                shot.status = "passed" if qc.status == "pass" else "failed"
                if qc.status != "pass":
                    failed.append(shot)
                ctx.trace("shot_qc", shot=shot.shotId, status=qc.status, action=qc.repairAction, reason=qc.reason[:200])
            video_qc.repairRounds = round_index
            if not failed:
                break
            if round_index == settings.maxVideoRepairs:
                break
            for shot in failed:
                _apply_repair(shot, shot.qc or ShotQC())
            pending = failed
        hard = [s.shotId for s in plan.shotPlan if s.qc and s.qc.status == "hard_fail" and s.status != "passed"]
        soft = [s.shotId for s in plan.shotPlan if s.qc and s.qc.status == "soft_fail" and s.status != "passed"]
        if soft:
            video_qc.warnings.append(f"shots still flagged after {video_qc.repairRounds} repair round(s): {', '.join(soft)}")
        video_qc.overall = "fail" if hard else ("warn" if soft else "pass")
        plan.qc["video"] = video_qc.model_dump(mode="json")
        plan.warnings.extend(video_qc.warnings)
        if hard:   # PRD §15.3: identity drift / wrong product / severe defects must fail
            raise StageError(f"video QC hard failure on {', '.join(hard)} after {video_qc.repairRounds} repair round(s)")


class AssemblerStage(Stage):
    """Deterministic pipeline: normalize clips, hard-cut concat (jump cuts are the social-video idiom)."""

    name = "assembler"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        settings = plan.generationSettings
        normalized: list[Path] = []
        for shot in plan.shotPlan:
            if not shot.clipPath or not Path(shot.clipPath).is_file():
                raise StageError(f"{shot.shotId} has no clip to assemble")
            trim = shot.durationSec + 1.0 if (shot.qc and shot.qc.repairAction == "trim_boundary") else None
            dst = ctx.clips_dir / f"{shot.shotId}_norm.mp4"
            normalized.append(await asyncio.to_thread(normalize_clip, Path(shot.clipPath), dst, settings.aspectRatio, trim))
        final = await asyncio.to_thread(concat_clips, normalized, ctx.run_dir / "final.mp4")
        meta = ffprobe_metadata(final)
        plan.finalVideo = str(final)
        ctx.trace("assembled", clips=len(normalized), duration_s=meta["duration_s"])
        target = settings.targetDurationSec
        if abs(meta["duration_s"] - target) > 0.35 * target:
            plan.warnings.append(f"final video is {meta['duration_s']:.1f}s for a {target}s target")
