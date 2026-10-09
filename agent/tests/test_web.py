"""The local web UI end to end against the mock pipeline: generate, guided pause + override, final video."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from vidai.config import VidaiSettings
from vidai.web.server import serve

PAGE = (Path(__file__).parent / "test_pipeline_mock.py").read_text().split('PAGE = """')[1].split('"""')[0]


def _call(base: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def _wait(base: str, cid: str, until, timeout_s: float = 240.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        snap = json.loads(_call(base, f"/api/creative/{cid}")[2])
        if until(snap):
            return snap
        time.sleep(1.0)
    raise AssertionError("timed out waiting for the creative")


@pytest.fixture()
def site(tmp_path: Path) -> tuple[str, Path]:
    page = tmp_path / "site"
    page.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=1:duration=1",
                    "-frames:v", "1", str(page / "hero.jpg")], check=True)
    (page / "page.html").write_text(PAGE, encoding="utf-8")
    settings = VidaiSettings.from_env()
    settings.mock = True
    settings.data_dir = tmp_path / "data"
    server = serve(settings, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", page / "page.html"
    server.shutdown()


def test_ui_auto_mode_one_click(site) -> None:
    base, page = site
    status, ctype, body = _call(base, "/")
    assert status == 200 and "text/html" in ctype and b"MasSurge Studio" in body
    cid = json.loads(_call(base, "/api/generate", {"url": str(page), "mode": "auto", "duration": 15})[2])["creativeId"]
    snap = _wait(base, cid, lambda s: not s["running"] and not s["pausedAt"])
    assert snap["plan"]["status"] == "READY", snap["plan"].get("error")
    assert snap["finalVideoUrl"] and snap["heroUrl"] and snap["referenceUrl"]
    status, ctype, video = _call(base, snap["finalVideoUrl"])
    assert status == 200 and ctype == "video/mp4" and len(video) > 1000
    rows = json.loads(_call(base, "/api/creatives")[2])
    assert any(r["creativeId"] == cid and r["status"] == "READY" for r in rows)


def test_ui_guided_pauses_and_overrides(site) -> None:
    base, page = site
    cid = json.loads(_call(base, "/api/generate", {"url": str(page), "mode": "guided"})[2])["creativeId"]
    snap = _wait(base, cid, lambda s: s["pausedAt"] == "strategy")
    other = snap["plan"]["angleCandidates"][1]["angleId"]
    _call(base, f"/api/creative/{cid}/continue", {"angleId": other})
    snap = _wait(base, cid, lambda s: s["pausedAt"] == "character")
    assert snap["plan"]["sellingAngle"]["angleId"] == other
    _call(base, f"/api/creative/{cid}/continue", {})
    snap = _wait(base, cid, lambda s: s["pausedAt"] == "script_qc")
    beats = snap["plan"]["speechVisualBeats"]
    beats[0]["speech"] = "Edited hook line from the browser."
    _call(base, f"/api/creative/{cid}/continue", {"script": beats})
    snap = _wait(base, cid, lambda s: not s["running"] and not s["pausedAt"])
    plan = snap["plan"]
    assert plan["status"] == "READY", plan.get("error")
    assert plan["userOverrides"]["angle"] == other and plan["userOverrides"]["script"].startswith("edited")
    assert plan["speechVisualBeats"][0]["speech"] == "Edited hook line from the browser."
    # revise: regenerate one shot -> v2, only that shot re-rendered
    target = plan["shotPlan"][-1]["shotId"]
    before = {s["shotId"]: s["attempts"] for s in plan["shotPlan"]}
    _call(base, f"/api/creative/{cid}/revise", {"shotIds": [target]})
    snap = _wait(base, cid, lambda s: s["plan"]["version"] == 2 and not s["running"])
    assert snap["plan"]["status"] == "READY"
    for s in snap["plan"]["shotPlan"]:
        assert (s["attempts"] > before[s["shotId"]]) == (s["shotId"] == target)
