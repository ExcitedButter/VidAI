"""Local open-weights video backend: LTX-2.5 served by scripts/ltx_worker.py on localhost.

Same interface as the Seedance clients, so the pipeline does not care which model renders a shot.
LTX-2.5 generates the video and the spoken audio jointly from the prompt (dialogue in quotes).
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

import httpx

from vidai.agent.seedance import SeedanceClient, SeedanceNonRetryable, SeedanceResult

logger = logging.getLogger(__name__)

FPS = 24
# Two-stage pipelines generate stage 1 at half resolution, so both sides must be multiples of 64.
_SIZES = {
    "720p": {"9:16": (704, 1280), "16:9": (1280, 704), "1:1": (960, 960), "3:4": (960, 1280), "4:3": (1280, 960),
             "adaptive": (704, 1280)},
    "1080p": {"9:16": (1088, 1920), "16:9": (1920, 1088), "1:1": (1472, 1472), "3:4": (1408, 1920),
              "4:3": (1920, 1408), "adaptive": (1088, 1920)},
    "480p": {"9:16": (448, 832), "16:9": (832, 448), "1:1": (640, 640), "3:4": (640, 832), "4:3": (832, 640),
             "adaptive": (448, 832)},
}


def frame_count(duration_s: float, fps: int = FPS, minimum: int = 25) -> int:
    """Snap a duration to the VAE's causal grid (8k + 1 frames)."""
    frames = max(int(round(duration_s * fps)), minimum)
    return max(minimum, 8 * int(round((frames - 1) / 8)) + 1)


def output_size(ratio: str, resolution: str) -> tuple[int, int]:
    table = _SIZES.get(resolution, _SIZES["720p"])
    return table.get(ratio, table["adaptive"])


class LocalVideoClient(SeedanceClient):
    """Talks to the resident LTX-2.5 worker; clips land directly on the shared filesystem."""

    def __init__(self, *, base_url: str = "http://127.0.0.1:8765", timeout_s: float = 1800.0,
                 first_frame_strength: float = 1.0, model: str = "ltx-2.5-22b-distilled"):
        self.model = model
        self.mock = False
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.first_frame_strength = first_frame_strength
        self._client = None  # the parent's Ark client is not used

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    async def generate_to_file(
        self,
        *,
        prompt: str,
        out_path: Path,
        first_frame_image: Path | None = None,
        duration_s: int = 5,
        ratio: str = "adaptive",
        resolution: str = "720p",
        watermark: bool = False,
        generate_audio: bool = True,
    ) -> SeedanceResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = output_size(ratio, resolution)
        seed = int(hashlib.sha1(f"{prompt}|{out_path.name}".encode()).hexdigest()[:8], 16) % (2**31)
        payload = {
            "prompt": prompt, "width": width, "height": height, "num_frames": frame_count(duration_s),
            "fps": FPS, "seed": seed, "output_path": str(out_path.resolve()),
            "image": str(Path(first_frame_image).resolve()) if first_frame_image else None,
            "first_frame_strength": self.first_frame_strength,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(f"{self.base_url}/generate", json=payload)
        except httpx.TransportError as exc:
            raise RuntimeError(f"local video worker unreachable at {self.base_url}: {exc}") from exc
        if response.status_code == 400:
            raise SeedanceNonRetryable(f"local video worker rejected the request: {response.text[:300]}")
        if response.status_code >= 400:
            raise RuntimeError(f"local video worker failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        if not generate_audio:
            _strip_audio(out_path)
        logger.info("local clip %s in %ss", out_path.name, data.get("seconds"))
        return SeedanceResult(video_path=str(out_path), task_id=f"local-{seed}", video_url=None, prompt=prompt)

    async def close(self) -> None:
        return None


def _strip_audio(path: Path) -> None:
    silent = path.with_suffix(".noaudio.mp4")
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-an", "-c:v", "copy", str(silent)],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        silent.replace(path)
