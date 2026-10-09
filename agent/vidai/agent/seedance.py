"""Seedance video-generation client (Volcano Engine Ark / BytePlus ModelArk task API).

Real flow:  POST /contents/generations/tasks  ->  poll GET /contents/generations/tasks/{id}
            -> download `content.video_url` when status == "succeeded".
Mock flow:  synthesizes a small mp4 locally with ffmpeg so the full pipeline can be
            exercised without an API key.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from vidai.agent.providers.chat_completions import image_to_data_url

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}
_NON_RETRYABLE = {400, 401, 402, 403, 404, 422}


class SeedanceNonRetryable(RuntimeError):
    """Payment / auth / request errors: retrying the identical request cannot succeed."""

_RATIO_TO_SIZE = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "4:3": (960, 720),
    "3:4": (720, 960),
    "1:1": (960, 960),
    "21:9": (1260, 540),
    "adaptive": (1280, 720),
}


@dataclass(slots=True)
class SeedanceResult:
    video_path: str
    task_id: str
    video_url: str | None = None
    prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "task_id": self.task_id,
            "video_url": self.video_url,
            "prompt": self.prompt,
        }


class SeedanceClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        mock: bool = False,
        poll_interval_s: float = 5.0,
        timeout_s: float = 900.0,
    ):
        self.model = model
        self.mock = mock
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None
        if not mock:
            self._client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=120.0,
            )

    async def generate_to_file(
        self,
        *,
        prompt: str,
        out_path: Path,
        first_frame_image: Path | None = None,
        duration_s: int = 5,
        ratio: str = "adaptive",
        resolution: str = "1080p",
        watermark: bool = False,
        generate_audio: bool = False,
    ) -> SeedanceResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mock:
            return self._mock_generate(prompt, out_path, duration_s, ratio, generate_audio)

        assert self._client is not None
        text = (
            f"{prompt} --resolution {resolution} --duration {duration_s} "
            f"--ratio {ratio} --watermark {'true' if watermark else 'false'}"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if first_frame_image is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_data_url(first_frame_image)},
                    "role": "first_frame",
                }
            )

        create = await self._client.post(
            "/contents/generations/tasks",
            json={"model": self.model, "content": content},
        )
        if create.status_code >= 400:
            error_cls = SeedanceNonRetryable if create.status_code in _NON_RETRYABLE else RuntimeError
            raise error_cls(f"Seedance task create failed ({create.status_code}): {create.text[:500]}")
        task_id = create.json()["id"]
        logger.info("seedance task created: %s", task_id)

        video_url = await self._poll(task_id)
        await self._download(video_url, out_path)
        return SeedanceResult(
            video_path=str(out_path), task_id=task_id, video_url=video_url, prompt=prompt
        )

    async def _poll(self, task_id: str) -> str:
        assert self._client is not None
        deadline = asyncio.get_event_loop().time() + self.timeout_s
        while True:
            try:
                response = await self._client.get(f"/contents/generations/tasks/{task_id}")
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                # Transient poll failures must not kill a paid generation task.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in (429, 500, 502, 503, 504):
                    raise
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError(
                        f"seedance task {task_id} polling kept failing after {self.timeout_s}s"
                    ) from exc
                logger.warning("seedance poll transient error, retrying: %s", exc)
                await asyncio.sleep(self.poll_interval_s)
                continue
            data = response.json()
            status = data.get("status", "")
            if status == "succeeded":
                video_url = (data.get("content") or {}).get("video_url")
                if not video_url:
                    raise RuntimeError(f"task {task_id} succeeded but no video_url: {data}")
                return video_url
            if status in _TERMINAL_STATUSES:
                raise RuntimeError(f"seedance task {task_id} ended with {status}: {data.get('error')}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"seedance task {task_id} still {status} after {self.timeout_s}s")
            logger.info("seedance task %s: %s", task_id, status)
            await asyncio.sleep(self.poll_interval_s)

    async def _download(self, url: str, out_path: Path) -> None:
        async with httpx.AsyncClient(timeout=300.0) as download_client:
            async with download_client.stream("GET", url) as response:
                response.raise_for_status()
                with out_path.open("wb") as fh:
                    async for chunk in response.aiter_bytes(1 << 20):
                        fh.write(chunk)

    def _mock_generate(
        self, prompt: str, out_path: Path, duration_s: int, ratio: str, generate_audio: bool = False
    ) -> SeedanceResult:
        width, height = _RATIO_TO_SIZE.get(ratio, _RATIO_TO_SIZE["adaptive"])
        digest = hashlib.sha1(prompt.encode()).hexdigest()[:10]
        seconds = max(1, duration_s)
        inputs = ["-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=24:duration={seconds}"]
        encode = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if generate_audio:  # a tone stands in for speech so the audio QC rule sees a non-silent track
            inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
            encode += ["-c:a", "aac", "-shortest"]
        drawtext = ["-vf", f"drawtext=text='mock seedance {digest}':fontcolor=white:fontsize=28:x=20:y=20"]
        for extra in (drawtext, []):  # drawtext needs libfreetype; fall back to plain testsrc
            proc = subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, *extra, *encode, str(out_path)],
                                  capture_output=True, text=True)
            if proc.returncode == 0:
                break
        else:
            raise RuntimeError(f"mock ffmpeg synth failed: {proc.stderr[:500]}")
        return SeedanceResult(
            video_path=str(out_path), task_id=f"mock-{digest}", video_url=None, prompt=prompt
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class SeevioClient(SeedanceClient):
    """Seedance via the seevio.ai aggregator task API.

    Differences from Ark: POST /v1/videos/generations with an `input` object,
    poll GET /v1/tasks/{id} (statuses queued/generating/completed/failed),
    video at data.results[0], and image-to-video takes public image URLs only
    (no base64) — local first frames are staged on a temporary file host.
    """

    _TERMINAL = {"completed", "failed"}

    async def generate_to_file(
        self,
        *,
        prompt: str,
        out_path: Path,
        first_frame_image: Path | None = None,
        duration_s: int = 5,
        ratio: str = "adaptive",
        resolution: str = "1080p",
        watermark: bool = False,
        generate_audio: bool = False,
    ) -> SeedanceResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mock:
            return self._mock_generate(prompt, out_path, duration_s, ratio, generate_audio)

        assert self._client is not None
        payload_input: dict[str, Any] = {
            "prompt": prompt,
            "generation_type": "text-to-video",
            "duration": max(4, min(30, duration_s)),
            "aspect_ratio": ratio,
            "resolution": resolution,
            "generate_audio": generate_audio,
            "watermark": watermark,
        }
        if first_frame_image is not None:
            payload_input["generation_type"] = "image-to-video"
            payload_input["image_urls"] = [await self._stage_image(first_frame_image)]
            # seevio: Seedance 2.5 i2v only accepts adaptive ratio (frame decides it)
            if payload_input["aspect_ratio"] != "adaptive":
                logger.info(
                    "seevio i2v: forcing aspect_ratio %s -> adaptive", payload_input["aspect_ratio"]
                )
                payload_input["aspect_ratio"] = "adaptive"

        create = await self._client.post(
            "/v1/videos/generations",
            json={"model": self.model, "input": payload_input},
        )
        if create.status_code >= 400:
            error_cls = SeedanceNonRetryable if create.status_code in _NON_RETRYABLE else RuntimeError
            raise error_cls(f"seevio task create failed ({create.status_code}): {create.text[:500]}")
        task_id = create.json()["taskId"]
        logger.info("seevio task created: %s", task_id)

        video_url = await self._poll_seevio(task_id)
        await self._download(video_url, out_path)
        return SeedanceResult(
            video_path=str(out_path), task_id=task_id, video_url=video_url, prompt=prompt
        )

    async def _poll_seevio(self, task_id: str) -> str:
        assert self._client is not None
        deadline = asyncio.get_event_loop().time() + self.timeout_s
        while True:
            try:
                response = await self._client.get(f"/v1/tasks/{task_id}")
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code is not None and status_code not in (429, 500, 502, 503, 504):
                    raise
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError(
                        f"seevio task {task_id} polling kept failing after {self.timeout_s}s"
                    ) from exc
                logger.warning("seevio poll transient error, retrying: %s", exc)
                await asyncio.sleep(self.poll_interval_s)
                continue
            data = response.json()
            status = data.get("status", "")
            if status == "completed":
                results = (data.get("data") or {}).get("results") or []
                if not results:
                    raise RuntimeError(f"task {task_id} completed but no results: {data}")
                return results[0]
            if status in self._TERMINAL:
                raise RuntimeError(f"seevio task {task_id} ended with {status}: {data.get('error')}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"seevio task {task_id} still {status} after {self.timeout_s}s")
            logger.info("seevio task %s: %s", task_id, status)
            await asyncio.sleep(self.poll_interval_s)

    async def _stage_image(self, path: Path) -> str:
        """Upload a local image to tmpfiles.org (60-min retention) for URL-only i2v input."""
        async with httpx.AsyncClient(timeout=60.0) as up:
            with path.open("rb") as fh:
                resp = await up.post(
                    "https://tmpfiles.org/api/v1/upload", files={"file": (path.name, fh)}
                )
        resp.raise_for_status()
        page_url = resp.json()["data"]["url"]
        # page URL -> direct-download URL (tmpfiles serves the raw file under /dl/)
        direct = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        logger.info("staged first frame at %s", direct)
        return direct


def build_video_client(
    *, base_url: str, api_key: str, model: str, mock: bool = False
) -> SeedanceClient:
    """Pick the client implementation from the endpoint host."""
    cls = SeevioClient if "seevio" in base_url else SeedanceClient
    return cls(base_url=base_url, api_key=api_key, model=model, mock=mock)
