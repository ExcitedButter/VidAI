"""Standalone smoke test for the SeevioClient generation leg (no agent loop)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidai.agent.seedance import build_video_client
from vidai.config import VidaiSettings


async def main() -> None:
    image = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    settings = VidaiSettings.from_env()
    client = build_video_client(
        base_url=settings.seedance_base_url,
        api_key=settings.ark_api_key,
        model=settings.seedance_model,
    )
    out = Path("out/test_seevio.mp4")
    try:
        result = await client.generate_to_file(
            prompt="A sleek product hero shot on a wooden kitchen table, soft morning light, "
            "slow camera orbit, photorealistic",
            out_path=out,
            first_frame_image=image,
            duration_s=4,
            ratio="16:9",
            resolution="480p",
        )
    finally:
        await client.close()
    print("task:", result.task_id)
    print("video url:", result.video_url)
    print("saved:", result.video_path, Path(result.video_path).stat().st_size, "bytes")


if __name__ == "__main__":
    asyncio.run(main())
