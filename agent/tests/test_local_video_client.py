"""LocalVideoClient against a fake worker: request shape (64-multiples, 8k+1 frames) and error mapping."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from vidai.agent.seedance import SeedanceNonRetryable, build_video_client
from vidai.agent.video_local import LocalVideoClient, frame_count, output_size


def test_frame_and_size_rules() -> None:
    assert frame_count(4.0) == 97 and frame_count(5.0) == 121 and frame_count(8.0) == 193 and frame_count(0.5) == 25
    for res in ("480p", "720p", "1080p"):
        for ratio in ("9:16", "16:9", "1:1", "3:4", "4:3", "adaptive"):
            w, h = output_size(ratio, res)
            assert w % 64 == 0 and h % 64 == 0, (res, ratio)
    assert output_size("9:16", "720p") == (704, 1280) and output_size("16:9", "1080p") == (1920, 1088)


@pytest.fixture()
def fake_worker(tmp_path: Path):
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(req)
            if "reject" in req["prompt"]:
                body, code = json.dumps({"error": "AssertionError: bad resolution"}).encode(), 400
            else:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={req['width']}x{req['height']}:rate=24:duration=1",
                                "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-shortest", req["output_path"]], check=True)
                body, code = json.dumps({"output_path": req["output_path"], "seconds": 0.1}).encode(), 200
            self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):  # silence
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def test_local_client_requests_and_writes_clip(tmp_path: Path, fake_worker) -> None:
    url, received = fake_worker
    client = build_video_client(base_url=url, api_key="", model="ltx-2.5-22b-distilled", backend="local")
    assert isinstance(client, LocalVideoClient)
    frame = tmp_path / "ref.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1:duration=1", "-frames:v", "1", str(frame)], check=True)
    out = tmp_path / "clips" / "shot_01_a01.mp4"
    result = asyncio.run(client.generate_to_file(prompt='She says: "hi"', out_path=out, first_frame_image=frame,
                                                 duration_s=5, ratio="9:16", resolution="720p", generate_audio=True))
    assert Path(result.video_path).is_file() and received[0]["image"] == str(frame.resolve())
    assert (received[0]["width"], received[0]["height"], received[0]["num_frames"], received[0]["fps"]) == (704, 1280, 121, 24)
    with pytest.raises(SeedanceNonRetryable):
        asyncio.run(client.generate_to_file(prompt="please reject", out_path=tmp_path / "x.mp4", duration_s=4))
    with pytest.raises(RuntimeError):
        asyncio.run(LocalVideoClient(base_url="http://127.0.0.1:1").generate_to_file(prompt="p", out_path=tmp_path / "y.mp4"))
