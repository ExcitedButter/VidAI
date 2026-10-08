"""Per-creative workspace shared by every pipeline stage."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from vidai.agent.providers.base import JsonClient, VisionClient
from vidai.agent.seedance import SeedanceClient
from vidai.config import VidaiSettings
from vidai.plan import CreativePlan

PauseCallback = Callable[[CreativePlan, str], Optional[Awaitable[None]]]


def slugify_hint(text: str, max_len: int = 40) -> str:
    """ASCII slug for file names (product name or URL)."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:max_len] or "creative"


class PipelineHalt(Exception):
    """Raised from an `on_pause` callback to stop after the current pause point (resume later)."""


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

    # ---------------------------------------------------------------- dirs
    @property
    def product_dir(self) -> Path:
        return self.run_dir / "product"

    @property
    def clips_dir(self) -> Path:
        return self.run_dir / "clips"

    @property
    def frames_dir(self) -> Path:
        return self.run_dir / "frames"

    @property
    def stages_dir(self) -> Path:
        return self.run_dir / "stages"

    @property
    def plan_path(self) -> Path:
        return self.run_dir / "plan.json"

    def ensure_dirs(self) -> None:
        for directory in (self.run_dir, self.product_dir, self.clips_dir, self.frames_dir, self.stages_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if self.checkpoint_index == 0:
            self.checkpoint_index = len(list(self.stages_dir.glob("*.json")))

    # ---------------------------------------------------------------- trace / checkpoints
    def trace(self, kind: str, **payload: Any) -> None:
        event = {"ts": time.time(), "kind": kind, "status": self.plan.status.value, **payload}
        with (self.run_dir / "trace.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def checkpoint(self, label: str) -> Path:
        """Persist the plan after every stage so any step can be inspected / resumed (PRD §17.1)."""
        self.checkpoint_index += 1
        text = self.plan.to_json()
        self.plan_path.write_text(text, encoding="utf-8")
        path = self.stages_dir / f"{self.checkpoint_index:02d}_{label}.json"
        path.write_text(text, encoding="utf-8")
        return path

    async def pause(self, group: str) -> None:
        """Guided-mode pause point: the caller may inspect / override the recommended choice."""
        if self.on_pause is None:
            return
        result = self.on_pause(self.plan, group)
        if inspect.isawaitable(result):
            await result


def build_run_dir(creatives_root: Path, plan: CreativePlan) -> Path:
    """creatives/<creativeId>/vNN — every version keeps its own clips, checkpoints and trace."""
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
