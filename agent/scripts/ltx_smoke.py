"""Smoke-test the local LTX-2.5 worker: one short talking-head clip with spoken dialogue.

    python scripts/ltx_smoke.py [--url http://127.0.0.1:8765] [--out results/ltx_smoke.mp4] [--seconds 4]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vidai.agent.media import audio_mean_volume_db, ffprobe_metadata  # noqa: E402
from vidai.agent.video_local import LocalVideoClient  # noqa: E402

PROMPT = ('A woman in her early thirties with shoulder-length dark hair, wearing a sage green ribbed workout set, '
          'sits in a bright apartment living room with morning window light, filming a selfie-style phone video. '
          'Medium shot, single handheld camera, natural creator energy. She looks into the lens and says: '
          '"Okay, hotel coffee was quietly ruining my trips. This little thing fixed that." '
          'Photoreal, natural skin, lips move with the words, no captions.')


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--out", default="results/ltx_smoke.mp4")
    parser.add_argument("--seconds", type=int, default=4)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--image", default=None, help="optional first-frame image")
    args = parser.parse_args()
    client = LocalVideoClient(base_url=args.url)
    print("health:", await client.health())
    started = time.time()
    result = await client.generate_to_file(prompt=PROMPT, out_path=Path(args.out), duration_s=args.seconds,
                                           ratio=args.ratio, resolution=args.resolution,
                                           first_frame_image=Path(args.image) if args.image else None)
    meta = ffprobe_metadata(Path(result.video_path))
    print(f"clip: {result.video_path}  {meta['width']}x{meta['height']} {meta['duration_s']:.2f}s "
          f"{meta['fps']:.0f}fps audio={meta['has_audio']} mean_volume={audio_mean_volume_db(Path(result.video_path))} dB "
          f"wall={time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
