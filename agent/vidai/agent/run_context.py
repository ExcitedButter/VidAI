"""Per-creative workspace shared by every pipeline stage.

Run directory layout (one folder per pipeline step, in execution order):

    creatives/<creativeId>/vNN/
      plan.json                      canonical Creative Plan (PRD §16), rewritten after every stage
      01_product_intelligence/       scraped.json, images/, product_intelligence.json
      02_audience/                   audience_candidates.json
      03_selling_angles/             angle_candidates.json
      04_character/                  character_candidates.json, character_brief.json, character_reference.jpg
      05_script_strategy/            script_strategy.json
      06_script/                     beats.json, speech_visual_script.json
      07_script_qc/                  script_qc.json (+ round_N_qc.json)
      08_shot_plan/                  shot_plan.json, product_cutaway.jpg
      09_video_generation/           shot_XX_attempt_YY.mp4
      10_video_qc/                   video_qc.json, shot_XX_attempt_YY/{frame_*.jpg, verdict.json}
      11_final/                      final.mp4, normalized/, why_this_creative.json, metrics.json
      _trace.jsonl                   only when VIDAI_TRACE=1 (debugging)
"""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel

from vidai.agent.providers.base import JsonClient, VisionClient
from vidai.agent.seedance import SeedanceClient
from vidai.config import VidaiSettings
from vidai.plan import CreativePlan

PauseCallback = Callable[[CreativePlan, str], Optional[Awaitable[None]]]

STAGE_DIRS: dict[str, str] = {
    "product": "01_product_intelligence",
    "audience": "02_audience",
    "angles": "03_selling_angles",
    "character": "04_character",
    "strategy": "05_script_strategy",
    "script": "06_script",
    "script_qc": "07_script_qc",
    "shots": "08_shot_plan",
    "video": "09_video_generation",
    "video_qc": "10_video_qc",
    "final": "11_final",
}


def slugify_hint(text: str, max_len: int = 40) -> str:
    """ASCII slug for file names (product name or URL)."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:max_len] or "creative"


class PipelineHalt(Exception):
    """Raised from an `on_pause` callback to stop after the current pause point (resume later)."""


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


@dataclass(slots=True)
class PipelineContext:
    plan: CreativePlan
    settings: VidaiSettings
    llm: JsonClient
    vision: VisionClient
    seedance: SeedanceClient
    run_dir: Path
    on_pause: Optional[PauseCallback] = None
    checkpoint_index: int = field(default=0)

    # ---------------------------------------------------------------- stage folders
    def stage_dir(self, key: str) -> Path:
        directory = self.run_dir / STAGE_DIRS[key]
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_json(self, key: str, name: str, payload: Any) -> Path:
        """Write one stage output as pretty JSON (pydantic models are dumped)."""
        path = self.stage_dir(key) / name
        path.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @property
    def product_dir(self) -> Path:
        return self.stage_dir("product")

    @property
    def clips_dir(self) -> Path:
        return self.stage_dir("video")

    @property
    def frames_dir(self) -> Path:
        return self.stage_dir("video_qc")

    @property
    def final_dir(self) -> Path:
        return self.stage_dir("final")

    @property
    def plan_path(self) -> Path:
        return self.run_dir / "plan.json"

    def ensure_dirs(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- trace / checkpoints
    def trace(self, kind: str, **payload: Any) -> None:
        """Debug trace (VIDAI_TRACE=1): every module call, QC verdict and status change."""
        if not self.settings.trace:
            return
        event = {"ts": time.time(), "kind": kind, "status": self.plan.status.value, **payload}
        with (self.run_dir / "_trace.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def checkpoint(self, label: str = "") -> Path:
        """Persist the canonical plan after every stage (PRD §16: single source of truth)."""
        self.checkpoint_index += 1
        self.plan_path.write_text(self.plan.to_json(), encoding="utf-8")
        return self.plan_path

    async def pause(self, group: str) -> None:
        """Guided-mode pause point: the caller may inspect / override the recommended choice."""
        if self.on_pause is None:
            return
        result = self.on_pause(self.plan, group)
        if inspect.isawaitable(result):
            await result


def build_run_dir(creatives_root: Path, plan: CreativePlan) -> Path:
    """creatives/<creativeId>/vNN — every version keeps its own stage folders."""
    return creatives_root / plan.creativeId / f"v{plan.version:02d}"


def find_plan_path(creatives_root: Path, creative_id: str, version: int | None = None) -> Path:
    base = creatives_root / creative_id
    if not base.is_dir():
        raise FileNotFoundError(f"no creative {creative_id!r} under {creatives_root}")
    if version is not None:
        path = base / f"v{version:02d}" / "plan.json"
        if not path.is_file():
            raise FileNotFoundError(f"no version {version} for {creative_id!r}")
        return path
    versions = sorted(p for p in base.glob("v*") if (p / "plan.json").is_file())
    if not versions:
        raise FileNotFoundError(f"creative {creative_id!r} has no saved plan")
    return versions[-1] / "plan.json"
