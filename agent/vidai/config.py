"""Environment / runtime configuration for the MasSurge creative pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from vidai.plan import GenerationSettings

REPO_ROOT = Path(__file__).resolve().parent.parent

ARK_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader; existing environment variables win."""
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ and value:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


@dataclass(slots=True)
class VidaiSettings:
    # Video generation backend: "local" (LTX-2.5 worker, open weights), "seevio", "ark", or "auto" (by URL)
    video_backend: str = "auto"
    seedance_model: str = ""
    seedance_base_url: str = ARK_DEFAULT_BASE_URL
    ark_api_key: str = ""
    first_frame_strength: float = 1.0   # local backend: how hard the identity frame conditions frame 0
    # Planner / vision LLM: runs the structured-JSON modules and the Video QC judge
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    vlm_model: str = ""
    llm_reasoning_effort: str = ""   # low|medium|high|xhigh|max for reasoning models; empty = not sent
    # Creative defaults (PRD §16 generationSettings)
    mode: str = "guided"
    target_duration_s: int = 15
    aspect_ratio: str = "9:16"
    resolution: str = "720p"
    max_shot_s: float = 8.0          # stable clip length for the video model (PRD §13.4)
    hard_max_shot_s: float = 10.0    # never request a longer clip than this
    min_shot_s: float = 4.0          # shortest clip the video model returns (Seedance 2.5 on seevio: 4 s)
    words_per_minute: int = 150
    max_script_repairs: int = 2
    max_video_repairs: int = 2
    schema_repair_retries: int = 2   # PRD §19.1 "schema invalid -> one repair retry"
    generate_audio: bool = True      # talking head: the character speaks the line (PRD §14-15 audio / speech QC)
    continuity_reference: bool = True  # reuse the first clip's frame as the identity source for later shots (PRD §11.4, §14.3)
    product_cutaway: bool = True       # product close-ups start from the real product photo (exact SKU on screen)
    character_library: Path | None = None
    # Runtime
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    mock: bool = False

    @classmethod
    def from_env(cls) -> "VidaiSettings":
        load_dotenv()
        data_dir = _env("VIDAI_DATA_DIR")
        llm_base = _env("VIDAI_LLM_BASE_URL", _env("VIDAI_SEEDANCE_BASE_URL", ARK_DEFAULT_BASE_URL))
        # Only reuse the Ark key for the LLM when the LLM endpoint is Ark / BytePlus itself;
        # never send the video key to a third-party LLM host.
        same_vendor = any(host in llm_base for host in ("volces.com", "bytepluses.com"))
        llm_key = _env("VIDAI_LLM_API_KEY") or (_env("ARK_API_KEY") if same_vendor else "")
        llm_model = _env("VIDAI_LLM_MODEL", "doubao-1-5-vision-pro-32k")
        library = _env("VIDAI_CHARACTER_LIBRARY")
        backend = _env("VIDAI_VIDEO_BACKEND", "auto").lower()
        video_url = _env("VIDAI_SEEDANCE_BASE_URL", ARK_DEFAULT_BASE_URL)
        if backend == "local":
            video_url = _env("VIDAI_LOCAL_VIDEO_URL", "http://127.0.0.1:8765")
        return cls(
            video_backend=backend,
            seedance_model=_env("VIDAI_SEEDANCE_MODEL", "ltx-2.5-22b-distilled" if backend == "local" else "doubao-seedance-2-5-pro"),
            seedance_base_url=video_url,
            ark_api_key=_env("ARK_API_KEY"),
            first_frame_strength=_env_float("VIDAI_FIRST_FRAME_STRENGTH", 1.0),
            llm_base_url=llm_base,
            llm_api_key=llm_key,
            llm_model=llm_model,
            vlm_model=_env("VIDAI_VLM_MODEL", llm_model),
            llm_reasoning_effort=_env("VIDAI_REASONING_EFFORT"),
            mode=_env("VIDAI_MODE", "guided"),
            target_duration_s=_env_int("VIDAI_DURATION", 15),
            aspect_ratio=_env("VIDAI_ASPECT", "9:16"),
            resolution=_env("VIDAI_RESOLUTION", "720p"),
            max_shot_s=_env_float("VIDAI_MAX_SHOT_SEC", 8.0),
            hard_max_shot_s=_env_float("VIDAI_HARD_MAX_SHOT_SEC", 10.0),
            min_shot_s=_env_float("VIDAI_MIN_SHOT_SEC", 4.0),
            words_per_minute=_env_int("VIDAI_WPM", 150),
            max_script_repairs=_env_int("VIDAI_MAX_SCRIPT_REPAIRS", 2),
            max_video_repairs=_env_int("VIDAI_MAX_VIDEO_REPAIRS", 2),
            schema_repair_retries=_env_int("VIDAI_SCHEMA_REPAIR_RETRIES", 2),
            generate_audio=_env("VIDAI_GENERATE_AUDIO", "1") in {"1", "true", "yes"},
            continuity_reference=_env("VIDAI_CONTINUITY_REFERENCE", "1") in {"1", "true", "yes"},
            product_cutaway=_env("VIDAI_PRODUCT_CUTAWAY", "1") in {"1", "true", "yes"},
            character_library=Path(library).expanduser() if library else None,
            data_dir=Path(data_dir).expanduser() if data_dir else REPO_ROOT / "data",
            mock=_env("VIDAI_MOCK", "0") in {"1", "true", "yes"},
        )

    def generation_settings(self) -> GenerationSettings:
        return GenerationSettings(
            targetDurationSec=self.target_duration_s,
            aspectRatio=self.aspect_ratio,
            resolution=self.resolution,
            videoModel="mock" if self.mock else self.seedance_model,
            maxShotSec=self.max_shot_s,
            minShotSec=self.min_shot_s,
            wordsPerMinute=self.words_per_minute,
            maxScriptRepairs=self.max_script_repairs,
            maxVideoRepairs=self.max_video_repairs,
        )
