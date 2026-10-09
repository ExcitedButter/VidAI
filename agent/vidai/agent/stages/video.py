"""Stages J, K and the Assembler — generation with continuity, shot-level QC + repair, concat (PRD §14-15)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from vidai.agent.media import (audio_mean_volume_db, compose_on_canvas, concat_clips, extract_frames,
                               ffprobe_metadata, normalize_clip)
from vidai.agent.stages.shots import is_cutaway_shot, template_prompt
from vidai.agent.video_local import output_size
from vidai.agent.run_context import PipelineContext
from vidai.agent.seedance import SeedanceNonRetryable
from vidai.agent.stages.base import Stage, StageError, call_module
from vidai.plan import QCCheck, Shot, ShotQC, Status, VideoQC

logger = logging.getLogger(__name__)

SILENCE_DB = -45.0
# Soft fails the Repair Agent resolves in the edit instead of regenerating (PRD §14.4: jump cuts are the idiom).
ACCEPT_IN_EDIT = {"use_jump_cut", "trim_boundary"}
_SIMPLIFY_SUFFIX = (" Simplified action: the character simply holds the product up to the camera, "
                    "then brings it closer for a clear close-up while talking; no complex hand motion.")
_FRAMING_SUFFIX = " Use a slightly wider medium shot with the product and both hands fully in frame."
_ALT_TAKE_SUFFIX = " Alternative take: same scene and person, small natural variation in delivery."
_AUDIO_SUFFIX = " The character speaks the line aloud, clearly and naturally, lips moving with the words."


def _reference_image(character) -> Path | None:
    if character:
        for asset in character.visualReferenceAssets:
            if Path(asset).is_file():
                return Path(asset)
    return None


def _cutaway_frame(ctx: PipelineContext) -> Path | None:
    """The product hero photo composed onto a canvas of the target aspect (first frame for close-ups)."""
    product = ctx.plan.product
    if product is None or not product.heroImagePath or not Path(product.heroImagePath).is_file():
        return None
    out = ctx.run_dir / "product_cutaway.jpg"
    if out.is_file():
        return out
    try:
        return compose_on_canvas(Path(product.heroImagePath), out, output_size(ctx.plan.generationSettings.aspectRatio, "720p"))
    except Exception as exc:  # a broken photo must not block generation
        logger.warning("cutaway frame failed: %s", exc)
        return None


def _is_cutaway(ctx: PipelineContext, shot: Shot) -> bool:
    return is_cutaway_shot(shot, ctx.plan.product, ctx.settings) and _cutaway_frame(ctx) is not None


def _first_frame(ctx: PipelineContext, shot: Shot) -> Path | None:
    """PRD §14.3 continuity: product close-ups start on the real product photo; every other shot
    starts on the character identity reference when one exists."""
    if _is_cutaway(ctx, shot):
        return _cutaway_frame(ctx)
    return _reference_image(ctx.plan.character)


def _capture_identity_reference(ctx: PipelineContext, shot: Shot) -> None:
    """PRD §11.4 / §14.3: one identity source per creative. A character without reference assets gets
    the mid-frame of the first generated clip; every later shot starts from (and is judged against) it."""
    plan, character = ctx.plan, ctx.plan.character
    if not ctx.settings.continuity_reference or character is None or not shot.clipPath or _is_cutaway(ctx, shot):
        return   # a product cutaway has no face to anchor identity on
    source = plan.userOverrides.get("identitySourceShot")
    if _reference_image(character) is not None and source != shot.shotId:
        return   # library asset, or an earlier shot already provides the identity
    try:
        meta = ffprobe_metadata(Path(shot.clipPath))
        frames = extract_frames(Path(shot.clipPath), ctx.run_dir / "identity", 1, meta["duration_s"])
    except Exception as exc:  # a missing frame must not stop generation
        logger.warning("identity frame capture failed for %s: %s", shot.shotId, exc)
        return
    if not frames:
        return
    reference = ctx.run_dir / "character_reference.jpg"
    shutil.copy2(frames[0], reference)
    character.visualReferenceAssets = [str(reference)]
    plan.userOverrides["identitySourceShot"] = shot.shotId
    ctx.trace("identity_reference", shot=shot.shotId, path=str(reference))


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
                resolution=settings.resolution, generate_audio=ctx.settings.generate_audio,
            )
            shot.clipPath = result.video_path
            shot.status = "generated"
            ctx.trace("shot_generated", shot=shot.shotId, attempt=shot.attempts, clip=result.video_path,
                      first_frame=str(_first_frame(ctx, shot) or ""))
            return
        except SeedanceNonRetryable as exc:   # credits / auth / bad request: stop immediately
            ctx.trace("shot_error", shot=shot.shotId, attempt=shot.attempts, error=str(exc)[:300], retryable=False)
            raise StageError(f"generation failed for {shot.shotId}: {exc}") from exc
        except Exception as exc:
            last_error = exc
            ctx.trace("shot_error", shot=shot.shotId, attempt=shot.attempts, error=str(exc)[:300])
            await asyncio.sleep(min(2 ** attempt, 10))
    raise StageError(f"generation failed for {shot.shotId}: {last_error}")


async def _qc_shot(ctx: PipelineContext, shot: Shot, previous: Shot | None) -> ShotQC:
    """Rules (timing, audio) + VLM judge (identity / product / continuity / defects / speech), PRD §15.1."""
    plan = ctx.plan
    clip = Path(shot.clipPath or "")
    if not clip.is_file():
        return ShotQC(status="hard_fail", reason="clip missing", repairAction="retry_same_prompt")
    try:
        meta = ffprobe_metadata(clip)
    except Exception as exc:
        return ShotQC(status="hard_fail", reason=f"unreadable clip: {exc}", repairAction="retry_same_prompt")
    if meta["duration_s"] < 0.5:
        return ShotQC(status="hard_fail", reason="empty clip", repairAction="retry_same_prompt")
    checks: dict[str, QCCheck] = {}
    tolerance = max(1.0, 0.3 * shot.durationSec)
    if abs(meta["duration_s"] - shot.durationSec) > tolerance:
        checks["timing"] = QCCheck(status="warn", reason=f"clip {meta['duration_s']:.1f}s vs planned {shot.durationSec:.1f}s")
    else:
        checks["timing"] = QCCheck(status="pass", reason=f"{meta['duration_s']:.1f}s")
    if ctx.settings.generate_audio and shot.speech.strip():
        level = audio_mean_volume_db(clip) if meta["has_audio"] else None
        if not meta["has_audio"] or (level is not None and level < SILENCE_DB):
            reason = "no audio track" if not meta["has_audio"] else f"audio nearly silent ({level:.0f} dB)"
            checks["audio"] = QCCheck(status="fail", reason=reason)
            return ShotQC(status="soft_fail", checks=checks, repairAction="regenerate_audio",
                          reason=f"no audible speech: {reason}")
        checks["audio"] = QCCheck(status="pass", reason=f"mean volume {level:.0f} dB" if level is not None else "audio present")
    frames = extract_frames(clip, ctx.frames_dir / clip.stem, 3, meta["duration_s"])
    references: list[str] = []
    reference_image = _reference_image(plan.character)
    if reference_image and str(reference_image) not in frames:
        references.append(str(reference_image))
    if previous and previous.clipPath:
        references += sorted(str(p) for p in (ctx.frames_dir / Path(previous.clipPath).stem).glob("frame_*.jpg"))[-1:]
    cutaway = _is_cutaway(ctx, shot)
    product_photo = _cutaway_frame(ctx) if shot.productVisible else None
    if product_photo:
        references.append(str(product_photo))
    context = {
        "shotId": shot.shotId, "visualType": shot.visualType, "productVisible": shot.productVisible,
        "characterExpected": not cutaway,
        "speech": shot.speech, "product": plan.product.productName if plan.product else "",
        "productVisual": plan.product.visualDescription if plan.product else "",
        "continuityAnchors": shot.continuityAnchors, "candidateFrames": len(frames),
        "referenceFrames": len(references),
        "referenceNote": ("after the candidate frames: " + ", ".join(
            ([f"character identity source"] if reference_image else [])
            + (["last frame of the previous shot"] if previous and previous.clipPath else [])
            + (["product reference photo (the real product)"] if product_photo else []))) if references else "",
        "ruleChecks": {k: v.model_dump() for k, v in checks.items()},
    }
    try:
        qc = await call_module(ctx, "video_qc", context, model=ShotQC, image_paths=frames + references,
                               purpose="video_qc")
    except StageError as exc:
        plan.warnings.append(f"{shot.shotId}: video QC judge unavailable ({str(exc)[:120]}); clip accepted on rules only")
        qc = ShotQC(status="pass", reason="judge unavailable; rule checks only")
    qc.checks = {**qc.checks, **checks}
    return qc


def _product_failed(qc: ShotQC) -> bool:
    check = qc.checks.get("product")
    return bool(check and check.status == "fail") or "product" in (qc.reason or "").lower()[:120]


def _apply_repair(shot: Shot, qc: ShotQC, ctx: PipelineContext | None = None) -> None:
    """Repair Agent policy (PRD §15.2): fix the failing shot, never redo the whole video."""
    action = qc.repairAction or "retry_same_prompt"
    shot.repairHistory.append(f"attempt {shot.attempts}: {action} ({qc.reason[:80]})")
    product = ctx.plan.product if ctx else None
    if ctx and product and shot.productVisible and _product_failed(qc):
        # The generator never saw the product: restate what it looks like; on a second product
        # failure make the shot a close-up cutaway that starts on the real product photo.
        visual = product.visualDescription or product.productName
        if shot.attempts >= 2 and _cutaway_frame(ctx) is not None and ctx.settings.product_cutaway:
            shot.visualType = "product_close_up"
            shot.prompt = template_prompt(shot, ctx.plan.character, product.productName, visual, cutaway=True)
            shot.repairHistory.append(f"attempt {shot.attempts}: product still wrong -> cutaway from the product photo")
        elif not shot.prompt.startswith("PRODUCT:"):
            shot.prompt = f"PRODUCT: {visual}. The product must look exactly like this, no other object. " + shot.prompt
        shot.status = "planned"
        return
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
    elif action == "regenerate_audio":
        if _AUDIO_SUFFIX not in shot.prompt:
            shot.prompt += _AUDIO_SUFFIX
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
            for shot in sorted(pending, key=plan.shotPlan.index):
                await _generate_shot(ctx, shot)
                _capture_identity_reference(ctx, shot)
            ctx.checkpoint(f"video_gen_r{round_index}")
            plan.status = Status.VIDEO_QC
            failed: list[Shot] = []
            for index, shot in enumerate(plan.shotPlan):
                if shot not in pending:
                    continue
                previous = plan.shotPlan[index - 1] if index > 0 else None
                qc = await _qc_shot(ctx, shot, previous)
                if qc.status == "soft_fail" and qc.repairAction in ACCEPT_IN_EDIT and settings.allowJumpCuts:
                    shot.repairHistory.append(f"attempt {shot.attempts}: accepted, resolved in edit ({qc.repairAction})")
                    qc.status = "pass"
                    qc.reason = f"accepted via {qc.repairAction}: {qc.reason}"
                shot.qc = qc
                video_qc.shots[shot.shotId] = qc
                shot.status = "passed" if qc.status == "pass" else "failed"
                if qc.status != "pass":
                    failed.append(shot)
                ctx.trace("shot_qc", shot=shot.shotId, status=qc.status, action=qc.repairAction, reason=qc.reason[:200])
            video_qc.repairRounds = round_index
            if not failed or round_index == settings.maxVideoRepairs:
                break
            for shot in failed:
                _apply_repair(shot, shot.qc or ShotQC(), ctx)
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
            trim = max(shot.speechSec + 0.6, 1.0) if (shot.qc and shot.qc.repairAction == "trim_boundary") else None
            dst = ctx.clips_dir / f"{shot.shotId}_norm.mp4"
            normalized.append(await asyncio.to_thread(normalize_clip, Path(shot.clipPath), dst, settings.aspectRatio, trim))
        final = await asyncio.to_thread(concat_clips, normalized, ctx.run_dir / "final.mp4")
        meta = ffprobe_metadata(final)
        plan.finalVideo = str(final)
        ctx.trace("assembled", clips=len(normalized), duration_s=meta["duration_s"], has_audio=meta["has_audio"])
        target = settings.targetDurationSec
        if abs(meta["duration_s"] - target) > 0.35 * target:
            plan.warnings.append(f"final video is {meta['duration_s']:.1f}s for a {target}s target")
        if ctx.settings.generate_audio and not meta["has_audio"]:
            plan.warnings.append("final video has no audio track")
