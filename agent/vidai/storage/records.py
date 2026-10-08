"""Persist finished creatives as records + manifest rows."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from vidai.plan import CreativePlan, Status, utc_now_iso
from vidai.storage.layout import DataLayout


def compute_metrics(plan: CreativePlan, final_duration_s: float | None = None) -> dict[str, Any]:
    """PRD §21 MVP evaluation metrics, per creative (aggregate over records for the rates)."""
    from datetime import datetime

    started = [e.startedAt for e in plan.history if e.startedAt]
    finished = [e.finishedAt for e in plan.history if e.finishedAt]
    time_to_ready = None
    if started and finished and plan.status == Status.READY:
        try:
            time_to_ready = round((datetime.fromisoformat(finished[-1]) - datetime.fromisoformat(started[0])).total_seconds(), 1)
        except ValueError:
            time_to_ready = None
    shots = len(plan.shotPlan)
    attempts = sum(s.attempts for s in plan.shotPlan)
    overrides = plan.userOverrides
    intervened = any(k in overrides for k in ("angle", "character", "script", "regeneratedShots",
                                               "regeneratedScript", "regeneratedBeats"))
    script_qc, video_qc = plan.script_qc, plan.video_qc
    return {
        "timeToReadySec": time_to_ready,
        "strategyAccepted": "angle" not in overrides,
        "characterAccepted": "character" not in overrides,
        "scriptEdited": any(k in overrides for k in ("script", "regeneratedScript", "regeneratedBeats")),
        "generationSucceededWithoutIntervention": plan.status == Status.READY and not intervened,
        "shotRetryRate": round((attempts - shots) / shots, 3) if shots else None,
        "clipsGenerated": attempts,
        "shots": shots,
        "scriptRepairRounds": script_qc.repairRounds if script_qc else None,
        "videoRepairRounds": video_qc.repairRounds if video_qc else None,
        "finalDurationSec": final_duration_s,
        "warnings": len(plan.warnings),
    }


def write_record(layout: DataLayout, plan: CreativePlan, run_dir: Path) -> Path:
    record_id = f"{plan.creativeId}_v{plan.version:02d}"
    record_dir = layout.records_dir / record_id
    record_dir.mkdir(parents=True, exist_ok=True)

    final_video: str | None = None
    final_duration: float | None = None
    if plan.finalVideo and Path(plan.finalVideo).is_file():
        dest = record_dir / "final.mp4"
        shutil.copy2(plan.finalVideo, dest)
        final_video = str(dest)
        try:
            from vidai.agent.media import ffprobe_metadata

            final_duration = ffprobe_metadata(dest)["duration_s"]
        except Exception:  # metrics must never block the record
            final_duration = None
    metrics = compute_metrics(plan, final_duration)
    (record_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (record_dir / "plan.json").write_text(plan.to_json(), encoding="utf-8")
    (record_dir / "why_this_creative.json").write_text(
        json.dumps(plan.why_this_creative(), indent=2, ensure_ascii=False), encoding="utf-8")
    trace = run_dir / "trace.jsonl"
    if trace.is_file():
        shutil.copy2(trace, record_dir / "trace.jsonl")

    angle, character = plan.sellingAngle, plan.character
    row: dict[str, Any] = {
        "id": record_id,
        "created_at": utc_now_iso(),
        "creativeId": plan.creativeId,
        "version": plan.version,
        "status": plan.status.value,
        "mode": plan.mode,
        "productUrl": plan.productUrl,
        "product": plan.product.productName if plan.product else None,
        "angle": angle.angleFamily if angle else None,
        "archetype": plan.scriptStrategy.archetype if plan.scriptStrategy else None,
        "character": character.name if character else None,
        "shots": len(plan.shotPlan),
        "final_video": final_video,
        "error": plan.error,
        "metrics": metrics,
        "run_dir": str(run_dir),
        "record_dir": str(record_dir),
    }
    (record_dir / "record.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
    append_manifest_row(layout, row)
    return record_dir


def append_manifest_row(layout: DataLayout, row: dict[str, Any]) -> None:
    with layout.manifest_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
