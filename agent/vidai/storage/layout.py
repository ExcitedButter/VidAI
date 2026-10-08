"""Local data-root layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class DataLayout:
    root: Path

    @property
    def creatives_dir(self) -> Path:
        """Working dirs: creatives/<creativeId>/vNN/{plan.json, stages/, clips/, frames/, product/, trace.jsonl}."""
        return self.root / "creatives"

    @property
    def records_dir(self) -> Path:
        """Finished creatives (READY / FAILED): plan, explanation, final video, trace."""
        return self.root / "records"

    @property
    def manifest_path(self) -> Path:
        return self.root / "records_manifest.jsonl"

    def ensure(self) -> "DataLayout":
        self.creatives_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        return self
