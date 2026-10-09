"""Local web UI: one click from a product URL to a talking-head creative (PRD §18 screens 1-5).

Zero extra dependencies: a stdlib HTTP server serves `static/index.html` and a small JSON API;
each creative runs the normal `run_creative` pipeline in its own thread. Guided mode pauses at
angle / character / script and waits for the browser to continue (with or without overrides).

    python -m vidai.web [--host 127.0.0.1] [--port 8080] [--mock]
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from vidai.agent.harness import prepare_revision, resume_group
from vidai.agent.run_context import PipelineHalt
from vidai.agent.single_run import load_plan, new_plan, run_creative
from vidai.config import VidaiSettings
from vidai.plan import CreativePlan, SpeechVisualBeat, Status
from vidai.storage import DataLayout

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class RunState:
    plan: CreativePlan
    thread: Optional[threading.Thread] = None
    paused_at: Optional[str] = None
    resume: threading.Event = field(default_factory=threading.Event)
    choice: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    run_dir: Optional[Path] = None
    record_dir: Optional[Path] = None
    started: float = field(default_factory=time.time)

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())


class App:
    """Owns the running creatives; one pipeline thread per creative."""

    def __init__(self, settings: VidaiSettings):
        self.settings = settings
        self.runs: dict[str, RunState] = {}
        self.lock = threading.Lock()
        DataLayout(settings.data_dir).ensure()

    # ------------------------------------------------------------------ runs
    def generate(self, body: dict[str, Any]) -> dict[str, Any]:
        url = (body.get("url") or "").strip()
        if not url:
            raise ValueError("url is required")
        plan = new_plan(self.settings, url, mode=body.get("mode") or None,
                        duration=int(body["duration"]) if body.get("duration") else None,
                        product_text=body.get("productText") or "", new_character=bool(body.get("newCharacter")))
        self._start(plan, "product")
        return {"creativeId": plan.creativeId}

    def continue_run(self, creative_id: str, body: dict[str, Any]) -> dict[str, Any]:
        state = self.runs.get(creative_id)
        if state is None or not state.paused_at:
            raise ValueError("creative is not paused")
        state.choice = dict(body or {})
        state.resume.set()
        return {"ok": True}

    def revise(self, creative_id: str, body: dict[str, Any]) -> dict[str, Any]:
        state = self.runs.get(creative_id)
        if state and state.running:
            raise ValueError("creative is still running")
        plan = state.plan if state else load_plan(self.settings, creative_id)[0]
        start = prepare_revision(
            plan, angle_id=body.get("angleId"), character_id=body.get("characterId"),
            script_beats=body.get("script"), shot_ids=body.get("shotIds"),
            regenerate_script=bool(body.get("regenerateScript")), regenerate_beats=body.get("beatIds"),
        )
        self._start(plan, start)
        return {"creativeId": plan.creativeId, "version": plan.version, "startGroup": start}

    def resume(self, creative_id: str) -> dict[str, Any]:
        state = self.runs.get(creative_id)
        if state and state.running:
            raise ValueError("creative is still running")
        plan = state.plan if state else load_plan(self.settings, creative_id)[0]
        start = resume_group(plan)
        if start is None:
            raise ValueError("creative is already READY")
        self._start(plan, start)
        return {"creativeId": plan.creativeId, "startGroup": start}

    def _start(self, plan: CreativePlan, start_group: str) -> RunState:
        state = RunState(plan=plan)

        def on_pause(p: CreativePlan, group: str) -> None:
            state.paused_at = group
            state.resume.clear()
            state.resume.wait()            # the pipeline thread sleeps until the browser continues
            state.paused_at = None
            choice, state.choice = state.choice, {}
            if choice.get("stop"):
                raise PipelineHalt()
            if choice.get("angleId"):
                p.select_angle(choice["angleId"], by_user=True)
            if choice.get("characterId"):
                p.select_character(choice["characterId"], by_user=True)
            if choice.get("script"):
                p.speechVisualBeats = [SpeechVisualBeat.model_validate(b) for b in choice["script"]]
                p.userOverrides["pendingScriptQC"] = True

        def worker() -> None:
            try:
                _, run_dir, record_dir = asyncio.run(run_creative(
                    plan, self.settings, start_group=start_group,
                    on_pause=on_pause if plan.mode == "guided" else None))
                state.run_dir, state.record_dir = run_dir, record_dir
            except Exception as exc:  # the pipeline normally turns failures into FAILED; this is the last net
                logger.exception("creative %s crashed", plan.creativeId)
                state.error = f"{type(exc).__name__}: {exc}"

        state.thread = threading.Thread(target=worker, name=f"creative-{plan.creativeId}", daemon=True)
        with self.lock:
            self.runs[plan.creativeId] = state
        state.thread.start()
        return state

    # ------------------------------------------------------------------ views
    def snapshot(self, creative_id: str) -> dict[str, Any]:
        state = self.runs.get(creative_id)
        if state is None:
            plan, _ = load_plan(self.settings, creative_id)
            data = json.loads(plan.to_json())
            return {"plan": data, "running": False, "pausedAt": None, "error": None, **self._media(data)}
        try:
            data = json.loads(state.plan.to_json())
        except Exception:  # serialized while the pipeline thread mutates it: fall back to disk
            path = self._plan_path(state.plan)
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"status": state.plan.status.value}
        return {"plan": data, "running": state.running, "pausedAt": state.paused_at, "error": state.error,
                "elapsedSec": round(time.time() - state.started, 1), **self._media(data)}

    def list_creatives(self) -> list[dict[str, Any]]:
        manifest = DataLayout(self.settings.data_dir).manifest_path
        rows: dict[str, dict[str, Any]] = {}
        if manifest.is_file():
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    rows[row["creativeId"]] = row
        for cid, state in self.runs.items():
            rows[cid] = {"creativeId": cid, "status": state.plan.status.value,
                         "product": state.plan.product.productName if state.plan.product else state.plan.productUrl,
                         "version": state.plan.version, "running": state.running}
        return sorted(rows.values(), key=lambda r: r.get("created_at", ""), reverse=True)

    def media_path(self, creative_id: str, kind: str, name: str = "") -> Optional[Path]:
        state = self.runs.get(creative_id)
        plan = state.plan if state else load_plan(self.settings, creative_id)[0]
        if kind == "final" and plan.finalVideo:
            return Path(plan.finalVideo)
        if kind == "reference" and plan.character and plan.character.visualReferenceAssets:
            return Path(plan.character.visualReferenceAssets[0])
        if kind == "hero" and plan.product and plan.product.heroImagePath:
            return Path(plan.product.heroImagePath)
        if kind == "clip":
            for shot in plan.shotPlan:
                if shot.shotId == name and shot.clipPath:
                    return Path(shot.clipPath)
        return None

    def _media(self, data: dict[str, Any]) -> dict[str, Any]:
        cid = data.get("creativeId")
        return {"finalVideoUrl": f"/media/{cid}/final" if data.get("finalVideo") else None,
                "referenceUrl": f"/media/{cid}/reference" if (data.get("character") or {}).get("visualReferenceAssets") else None,
                "heroUrl": f"/media/{cid}/hero" if (data.get("product") or {}).get("heroImagePath") else None}

    def _plan_path(self, plan: CreativePlan) -> Path:
        return DataLayout(self.settings.data_dir).creatives_dir / plan.creativeId / f"v{plan.version:02d}" / "plan.json"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, content_type: Optional[str] = None) -> None:
            if not path.is_file():
                self._json(404, {"error": "not found"})
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def do_GET(self) -> None:  # noqa: N802
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                if not parts:
                    self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                elif parts[0] == "api" and parts[1:] == ["creatives"]:
                    self._json(200, app.list_creatives())
                elif parts[0] == "api" and len(parts) == 3 and parts[1] == "creative":
                    self._json(200, app.snapshot(parts[2]))
                elif parts[0] == "media" and len(parts) >= 3:
                    path = app.media_path(parts[1], parts[2], parts[3] if len(parts) > 3 else "")
                    self._file(path) if path else self._json(404, {"error": "no media"})
                else:
                    self._json(404, {"error": "unknown path"})
            except FileNotFoundError as exc:
                self._json(404, {"error": str(exc)})
            except Exception as exc:
                logger.exception("GET %s failed", self.path)
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self) -> None:  # noqa: N802
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                body = self._body()
                if parts == ["api", "generate"]:
                    self._json(200, app.generate(body))
                elif len(parts) == 4 and parts[:2] == ["api", "creative"] and parts[3] == "continue":
                    self._json(200, app.continue_run(parts[2], body))
                elif len(parts) == 4 and parts[:2] == ["api", "creative"] and parts[3] == "revise":
                    self._json(200, app.revise(parts[2], body))
                elif len(parts) == 4 and parts[:2] == ["api", "creative"] and parts[3] == "resume":
                    self._json(200, app.resume(parts[2]))
                else:
                    self._json(404, {"error": "unknown path"})
            except (ValueError, KeyError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                logger.exception("POST %s failed", self.path)
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug(fmt, *args)

    return Handler


def serve(settings: VidaiSettings, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(App(settings)))
    logger.info("MasSurge UI at http://%s:%d", host, server.server_port)
    return server
