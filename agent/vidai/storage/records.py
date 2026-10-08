"""Persist finished creatives as records + manifest rows."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from vidai.plan import CreativePlan, utc_now_iso
from vidai.storage.layout import DataLayout


def write_record(layout: DataLayout, plan: CreativePlan, run_dir: Path) -> Path:
    record_id = f"{plan.creativeId}_v{plan.version:02d}"
    record_dir = layout.records_dir / record_id
    record_dir.mkdir(parents=True, exist_ok=True)

    final_video: str | None = None
    if plan.finalVideo and Path(plan.finalVideo).is_file():
        dest = record_dir / "final.mp4"
        shutil.copy2(plan.finalVideo, dest)
        final_video = str(dest)
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
        "run_dir": str(run_dir),
        "record_dir": str(record_dir),
    }
    (record_dir / "record.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
    append_manifest_row(layout, row)
    return record_dir


def append_manifest_row(layout: DataLayout, row: dict[str, Any]) -> None:
    with layout.manifest_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
