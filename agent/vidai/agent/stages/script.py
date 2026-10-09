"""Stages E and F — Beats -> Copy, then Script QC with targeted repair (PRD §8, §9)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from vidai.agent.run_context import PipelineContext
from vidai.agent.stages.base import Stage, StageError, call_module, speech_seconds
from vidai.plan import VISUAL_TYPES, Beat, QCCheck, ScriptQC, SpeechVisualBeat


class _BeatsReply(BaseModel):
    beats: list[Beat]


class _CopyReply(BaseModel):
    speechVisualBeats: list[SpeechVisualBeat]


class _QCReply(BaseModel):
    overall: str = "pass"
    checks: dict[str, QCCheck] = {}
    repairInstructions: list[str] = []


def _strategy_payload(plan) -> dict[str, Any]:
    return {
        "product": plan.product, "audience": plan.audience, "sellingAngle": plan.sellingAngle,
        "scriptStrategy": plan.scriptStrategy, "character": plan.character,
        "durationSec": plan.generationSettings.targetDurationSec,
    }


def _recompute_durations(beats: list[SpeechVisualBeat], wpm: int) -> float:
    total = 0.0
    for beat in beats:
        beat.estimatedDurationSec = speech_seconds(beat.speech, wpm)
        total += beat.estimatedDurationSec
    return round(total, 2)


class BeatPlannerStage(Stage):
    name = "beat_planner"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        if plan.scriptStrategy is None:
            raise StageError("no script strategy")
        reply = await call_module(
            ctx, "beat_planner",
            {**_strategy_payload(plan), "maxShotSec": plan.generationSettings.maxShotSec},
            model=_BeatsReply,
        )
        beats = reply.beats
        if len(beats) < 2:
            raise StageError("beat planner returned fewer than 2 beats")
        seen: set[str] = set()
        for index, beat in enumerate(beats, start=1):
            if not beat.beatId or beat.beatId in seen:
                beat.beatId = f"b{index}"
            seen.add(beat.beatId)
            if beat.visualIntent not in VISUAL_TYPES:
                beat.visualIntent = "talking_head"
        if not any(b.productRequired for b in beats):
            # PRD §8.5: the product must appear at least when the core benefit is explained.
            middle = beats[min(len(beats) // 2, len(beats) - 1)]
            middle.productRequired = True
            middle.visualIntent = "hold_product"
        plan.beats = beats
        ctx.save_json("script", "beats.json", beats)


class CopywriterStage(Stage):
    name = "copywriter"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        settings = plan.generationSettings
        reply = await call_module(
            ctx, "copywriter",
            {**_strategy_payload(plan), "beats": plan.beats,
             "wordsPerMinute": settings.wordsPerMinute, "maxShotSec": settings.maxShotSec},
            model=_CopyReply,
        )
        plan.speechVisualBeats = _align_with_beats(reply.speechVisualBeats, plan.beats)
        _recompute_durations(plan.speechVisualBeats, settings.wordsPerMinute)
        _save_script(ctx)


def _save_script(ctx: PipelineContext) -> None:
    """06_script/speech_visual_script.json always holds the current (possibly repaired) script."""
    plan = ctx.plan
    ctx.save_json("script", "speech_visual_script.json", {
        "estimatedDurationSec": round(sum(b.estimatedDurationSec for b in plan.speechVisualBeats), 2),
        "beats": plan.speechVisualBeats,
    })


def _align_with_beats(svb: list[SpeechVisualBeat], beats: list[Beat]) -> list[SpeechVisualBeat]:
    """Keep beat ids / product requirements from the outline authoritative."""
    by_id = {b.beatId: b for b in beats}
    out: list[SpeechVisualBeat] = []
    for index, item in enumerate(svb):
        beat = by_id.get(item.beatId) or (beats[index] if index < len(beats) else None)
        if beat is not None:
            item.beatId = beat.beatId
            item.purpose = item.purpose or beat.purpose
            if beat.productRequired:
                item.visual.productRequired = True
                if item.visual.type == "talking_head":
                    item.visual.type = beat.visualIntent if beat.visualIntent != "talking_head" else "hold_product"
        if item.visual.type not in VISUAL_TYPES:
            item.visual.type = "talking_head"
        out.append(item)
    return out


def rule_checks(plan) -> tuple[dict[str, QCCheck], list[str], list[str]]:
    """Deterministic QC rows (PRD §9.2 durationFit / productTiming + per-shot length)."""
    settings = plan.generationSettings
    beats = plan.speechVisualBeats
    checks: dict[str, QCCheck] = {}
    instructions: list[str] = []
    failed: list[str] = []
    total = sum(b.estimatedDurationSec for b in beats)
    target = settings.targetDurationSec
    low, high = target * 0.85, target * 1.15
    if total > high:
        pct = int(round((total - target) / total * 100))
        checks["durationFit"] = QCCheck(status="fail", reason=f"Estimated {total:.0f}s for {target}s target", beatIds=[b.beatId for b in beats])
        instructions.append(f"Reduce total word count by ~{pct}% so the script speaks in about {target}s; cut words, not beats")
        failed += [b.beatId for b in beats]
    elif total < low:
        pct = int(round((target - total) / max(total, 1) * 100))
        checks["durationFit"] = QCCheck(status="fail", reason=f"Estimated {total:.0f}s for {target}s target", beatIds=[b.beatId for b in beats])
        instructions.append(f"Extend the spoken copy by ~{pct}% (add specific experience / proof) to reach about {target}s")
        failed += [b.beatId for b in beats]
    else:
        checks["durationFit"] = QCCheck(status="pass", reason=f"Estimated {total:.0f}s for {target}s target")
    long_beats = [b.beatId for b in beats if b.estimatedDurationSec > settings.maxShotSec]
    if long_beats:
        checks["beatLength"] = QCCheck(status="fail", reason=f"beats longer than the {settings.maxShotSec:.0f}s clip limit", beatIds=long_beats)
        instructions.append(f"Shorten beats {', '.join(long_beats)} to under {settings.maxShotSec:.0f}s of speech each")
        failed += long_beats
    else:
        checks["beatLength"] = QCCheck(status="pass", reason="every beat fits one clip")
    product_beats = [i for i, b in enumerate(beats) if b.visual.productRequired or b.visual.type in ("hold_product", "wear_or_use_product", "product_close_up", "simple_demo")]
    if len(beats) > 2 and len(product_beats) == len(beats):
        # PRD §8.5: never make the character hold the product stiffly the whole time
        checks["productOveruse"] = QCCheck(status="warn", reason="product on screen in every beat; let at least the hook or payoff breathe")
    if not product_beats:
        checks["productTiming"] = QCCheck(status="fail", reason="the product never appears on screen")
        instructions.append("Add a product reveal (hold_product or product_close_up) to the beat that explains the core benefit")
    else:
        start = sum(b.estimatedDurationSec for b in beats[: product_beats[0]])
        if total and start / total > 0.6:
            checks["productTiming"] = QCCheck(status="fail", reason=f"first product moment only at {start:.0f}s of {total:.0f}s", beatIds=[beats[product_beats[0]].beatId])
            instructions.append("Move the first product reveal earlier, before the core benefit is explained")
            failed.append(beats[product_beats[0]].beatId)
        else:
            checks["productTiming"] = QCCheck(status="pass", reason=f"product first appears at {start:.0f}s")
    return checks, instructions, sorted(set(failed))


class ScriptQCStage(Stage):
    """Generator creates, Checker finds problems, Rewrite Agent fixes only what failed."""

    name = "script_qc"

    async def run(self, ctx: PipelineContext) -> None:
        plan = ctx.plan
        settings = plan.generationSettings
        regenerate = plan.userOverrides.pop("regenerateBeats", None)
        if regenerate:   # beat-level regenerate (PRD §22 P1): fresh take on those beats only, then normal QC
            repaired = await call_module(
                ctx, "script_repair",
                {**_strategy_payload(plan), "speechVisualBeats": plan.speechVisualBeats,
                 "repairInstructions": [f"Regenerate beats {', '.join(regenerate)} with a fresh take: new wording, "
                                        "same purpose, same visual plan; copy every other beat unchanged"],
                 "failedBeatIds": list(regenerate), "wordsPerMinute": settings.wordsPerMinute,
                 "maxShotSec": settings.maxShotSec},
                model=_CopyReply,
            )
            plan.speechVisualBeats = _align_with_beats(repaired.speechVisualBeats, plan.beats)
            _recompute_durations(plan.speechVisualBeats, settings.wordsPerMinute)
            plan.userOverrides["regeneratedBeats"] = list(regenerate)
            _save_script(ctx)
        qc = ScriptQC()
        for round_index in range(settings.maxScriptRepairs + 1):
            checks, instructions, failed = rule_checks(plan)
            total = _recompute_durations(plan.speechVisualBeats, settings.wordsPerMinute)
            reply = await call_module(
                ctx, "script_qc",
                {**_strategy_payload(plan), "speechVisualBeats": plan.speechVisualBeats,
                 "estimatedDurationSec": total,
                 "ruleChecks": {k: v.model_dump() for k, v in checks.items()}},
                model=_QCReply,
            )
            merged = {**reply.checks, **checks}   # rule rows are authoritative
            failing = [name for name, check in merged.items() if check.status == "fail"]
            llm_failed_beats = sorted({bid for name, c in reply.checks.items() if c.status == "fail" for bid in c.beatIds})
            qc = ScriptQC(
                overall="fail" if failing else "pass", checks=merged,
                repairInstructions=instructions + [i for i in reply.repairInstructions if i not in instructions],
                estimatedDurationSec=total, repairRounds=round_index, warnings=qc.warnings,
            )
            ctx.trace("script_qc", round=round_index, overall=qc.overall, failing=failing)
            ctx.save_json("script_qc", f"round_{round_index}_qc.json", qc)
            if not failing:
                break
            if round_index == settings.maxScriptRepairs:
                qc.warnings.append(f"Script QC still failing after {round_index} repair round(s): {', '.join(failing)}")
                plan.warnings.append(qc.warnings[-1])
                break
            repaired = await call_module(
                ctx, "script_repair",
                {**_strategy_payload(plan), "speechVisualBeats": plan.speechVisualBeats,
                 "repairInstructions": qc.repairInstructions, "failedBeatIds": sorted(set(failed + llm_failed_beats)),
                 "wordsPerMinute": settings.wordsPerMinute, "maxShotSec": settings.maxShotSec},
                model=_CopyReply,
            )
            plan.speechVisualBeats = _align_with_beats(repaired.speechVisualBeats, plan.beats)
            _recompute_durations(plan.speechVisualBeats, settings.wordsPerMinute)
            _save_script(ctx)
        plan.qc["script"] = qc.model_dump(mode="json")
        ctx.save_json("script_qc", "script_qc.json", qc)
