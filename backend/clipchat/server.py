"""Local HTTP API used by the Electron UI. Binds to 127.0.0.1 only.

Run: python -m clipchat.server --port 8765 --data-dir ~/.clipchat
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import queue
import secrets
import threading
import time
import tempfile
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import media as media_mod
from .clarify import clarify, combine
from .llm import ClaudeProvider, OllamaProvider, ProviderError, list_ollama_models
from .render import PREVIEW, build_srt, export_quality, render
from .timeline import EditError, Project
from .tools import RECIPES, Editor
from .transcribe import Transcriber

DEFAULT_SETTINGS = {
    "provider": "claude",
    "claude_model": "claude-opus-5-5",
    "ollama_model": "llama3.1:8b",
    "ollama_host": "http://127.0.0.1:11434",
    "api_key": "",
    "whisper_model": "base",
}


# Request bodies. Module level so FastAPI can resolve them (this file uses postponed annotations).

class ImportBody(BaseModel):
    paths: list[str]


class EditBody(BaseModel):
    tool: str
    args: dict = {}


class ChatBody(BaseModel):
    message: str


class SettingsBody(BaseModel):
    provider: str | None = None
    claude_model: str | None = None
    ollama_model: str | None = None
    ollama_host: str | None = None
    api_key: str | None = None


class ExportBody(BaseModel):
    path: str
    resolution: str = "full"  # full | 1080p | 720p
    quality: str = "high"  # high | standard | small
    subtitles: bool = True  # also save a .srt next to the video when there's speech


class State:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "thumbs").mkdir(exist_ok=True)
        self.settings_path = data_dir / "settings.json"
        self.project_path = data_dir / "project.json"
        self.settings = {**DEFAULT_SETTINGS, **self._read_json(self.settings_path)}
        if not self.settings_path.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
            # First run with no Claude key: if an on-device model is available, start there so chat just works.
            if list_ollama_models(self.settings["ollama_host"]):
                self.settings["provider"] = "ollama"
        self.lock = threading.RLock()
        self.status = ""
        self.editor = Editor(self._load_project(),
                             Transcriber(data_dir / "transcripts", self.settings["whisper_model"]),
                             on_status=self._set_status)
        self.provider = None
        self.clarifying: dict | None = None  # a question we asked before involving the model
        self.preview = {"version": -1, "rendering": False, "progress": 0.0, "error": None}
        self.preview_path = data_dir / "preview.mp4"
        self.jobs: dict[str, dict] = {}
        self.saved_version = self.editor.version
        threading.Thread(target=self._preview_loop, daemon=True).start()

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _load_project(self) -> Project:
        p = Project.from_dict(self._read_json(self.project_path)) if self.project_path.exists() else Project()
        # Drop anything whose source file has been moved or deleted since last session.
        missing = {mid for mid, m in p.media.items() if not Path(m.path).exists()}
        if missing:
            p.media = {k: v for k, v in p.media.items() if k not in missing}
            p.clips = [c for c in p.clips if c.media_id not in missing]
            if p.music and p.music.media_id in missing:
                p.music = None
        return p

    def _set_status(self, s: str) -> None:
        self.status = s

    def save_settings(self) -> None:
        self.settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def get_provider(self):
        s = self.settings
        want = (s["provider"], s["claude_model"] if s["provider"] == "claude" else s["ollama_model"])
        if self.provider is None or (self.provider.name, self.provider.model) != want:
            if s["provider"] == "claude":
                self.provider = ClaudeProvider(s["claude_model"], s["api_key"] or None)
            else:
                self.provider = OllamaProvider(s["ollama_model"], s["ollama_host"])
        return self.provider

    def snapshot(self) -> dict:
        e = self.editor
        return {
            "project": e.project.to_dict(),
            "duration": e.project.duration,
            "version": e.version,
            "can_undo": bool(e.history.undo_stack),
            "can_redo": bool(e.history.redo_stack),
            "status": self.status,
            "preview": self.preview,
        }

    def _preview_loop(self) -> None:
        """Re-render the low-res preview whenever the project changes."""
        while True:
            time.sleep(0.4)
            v = self.editor.version
            if v != self.saved_version:
                with self.lock:
                    self.project_path.write_text(json.dumps(self.editor.project.to_dict()), encoding="utf-8")
                self.saved_version = v
            if v == self.preview["version"]:
                continue
            with self.lock:
                project = copy.deepcopy(self.editor.project)
                words = self.editor.words_by_media(only_if_cached=True)
            if not project.clips:
                self.preview = {"version": v, "rendering": False, "progress": 0.0, "error": None, "empty": True}
                continue
            self.preview = {**self.preview, "rendering": True, "progress": 0.0, "error": None}
            tmp = self.preview_path.with_name(f"preview-{v}.mp4")
            try:
                render(project, tmp, PREVIEW, words,
                       on_progress=lambda p: self.preview.__setitem__("progress", round(p, 3)))
                os.replace(tmp, self.preview_path)
                self.preview = {"version": v, "rendering": False, "progress": 1.0, "error": None}
            except Exception as ex:  # noqa: BLE001 - surface any render failure in the UI
                tmp.unlink(missing_ok=True)
                self.preview = {"version": v, "rendering": False, "progress": 0.0, "error": str(ex)[-400:]}


def create_app(data_dir: Path, token: str) -> FastAPI:
    app = FastAPI(title="ClipChat")
    st = State(data_dir)
    app.state.clipchat = st
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        # Any web page can send requests to localhost; the per-launch token keeps them out.
        if request.method != "OPTIONS" and request.url.path != "/health":
            supplied = request.headers.get("x-clipchat-token") or request.query_params.get("token")
            if not supplied or not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "bad token"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/project")
    def get_project():
        return st.snapshot()

    @app.post("/import")
    def import_files(body: ImportBody):
        errors = []
        for p in body.paths:
            try:
                with st.lock:
                    st.editor.import_file(p)
            except (OSError, ValueError, RuntimeError) as e:
                errors.append(f"{Path(p).name}: {e}")
        return {**st.snapshot(), "errors": errors}

    @app.post("/edit")
    def edit(body: EditBody):
        with st.lock:
            result = st.editor.execute(body.tool, body.args)
        return {**st.snapshot(), "result": result}

    @app.post("/undo")
    def undo():
        with st.lock:
            try:
                st.editor.undo()
            except EditError as e:
                raise HTTPException(400, str(e)) from None
        return st.snapshot()

    @app.post("/redo")
    def redo():
        with st.lock:
            try:
                st.editor.redo()
            except EditError as e:
                raise HTTPException(400, str(e)) from None
        return st.snapshot()

    @app.post("/new")
    def new_project():
        with st.lock:
            st.editor.history.push(st.editor.project)
            st.editor.project = Project()
            st.editor.version += 1
            st.provider = None
        return st.snapshot()

    @app.post("/chat")
    def chat(body: ChatBody):
        """Streams newline-delimited JSON events while the model works."""
        events: queue.Queue = queue.Queue()

        def work():
            try:
                provider = st.get_provider()
                message = body.message
                with st.lock:
                    if st.clarifying:  # this message answers our clarifying question
                        message = combine(st.clarifying["text"], st.clarifying["question"], message)
                        st.clarifying = None
                    elif not provider.pending and (ask := clarify(message, st.editor.project)):
                        st.clarifying = {"text": message, "question": ask["question"]}
                        events.put({"type": "ask", **ask})
                        events.put({"type": "reply", "text": ask["question"]})
                        return
                    reply = provider.chat(st.editor, message, events.put)
                events.put({"type": "reply", "text": reply})
            except ProviderError as e:
                events.put({"type": "error", "text": str(e)})
            except Exception as e:  # noqa: BLE001
                events.put({"type": "error", "text": f"Something went wrong: {e}"})
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while True:
                try:
                    ev = events.get(timeout=0.5)
                except queue.Empty:
                    if st.status:
                        yield json.dumps({"type": "status", "text": st.status}) + "\n"
                    continue
                if ev is None:
                    yield json.dumps({"type": "done", "snapshot": st.snapshot()}) + "\n"
                    return
                yield json.dumps(ev, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/recipes")
    def recipes():
        return [{"key": k, "label": r["label"], "emoji": r["emoji"], "description": r["description"]}
                for k, r in RECIPES.items()]

    @app.post("/recipe/{key}")
    def run_recipe(key: str):
        with st.lock:
            try:
                results = st.editor.run_recipe(key)
            except EditError as e:
                raise HTTPException(400, str(e)) from None
        return {**st.snapshot(), "results": results}

    @app.post("/dictate")
    async def dictate(request: Request):
        """Speech-to-text for the chat box's microphone button. Runs locally with Whisper."""
        audio = await request.body()
        if not audio or len(audio) > 25_000_000:
            raise HTTPException(400, "Recording is empty or too long.")
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio)
        try:
            text = await run_in_threadpool(st.editor.transcriber.text, f.name)
        finally:
            os.unlink(f.name)
        return {"text": text}

    @app.post("/chat/reset")
    def chat_reset():
        st.provider = None
        st.clarifying = None
        return {"ok": True}

    @app.get("/settings")
    def get_settings():
        s = {k: v for k, v in st.settings.items() if k != "api_key"}
        s["has_api_key"] = bool(st.settings["api_key"] or os.environ.get("ANTHROPIC_API_KEY"))
        s["ollama_models"] = list_ollama_models(st.settings["ollama_host"])
        return s

    @app.post("/settings")
    def set_settings(body: SettingsBody):
        changes = body.model_dump(exclude_none=True)
        if "provider" in changes and changes["provider"] not in ("claude", "ollama"):
            raise HTTPException(400, "provider must be 'claude' or 'ollama'")
        st.settings.update(changes)
        st.save_settings()
        st.provider = None  # pick up the new model/key on the next message
        return get_settings()

    @app.get("/preview.mp4")
    def preview_file():
        if not st.preview_path.exists():
            raise HTTPException(404)
        return FileResponse(st.preview_path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @app.get("/thumb/{media_id}")
    def thumb(media_id: str):
        m = st.editor.project.media.get(media_id)
        if not m or m.kind == "audio":
            raise HTTPException(404)
        path = media_mod.thumbnail(m, st.data_dir / "thumbs" / f"{hashlib.sha1(m.path.encode()).hexdigest()[:16]}.jpg")
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/export")
    def export(body: ExportBody):
        out = Path(body.path)
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")
        with st.lock:
            project = copy.deepcopy(st.editor.project)
        if not project.clips:
            raise HTTPException(400, "The timeline is empty.")
        job_id = uuid.uuid4().hex[:8]
        job = st.jobs[job_id] = {"state": "running", "progress": 0.0, "path": str(out), "error": None}

        def work():
            try:
                with st.lock:
                    words = st.editor.words_by_media() if (project.captions.enabled or body.subtitles) else {}
                render(project, out, export_quality(body.resolution, body.quality), words,
                       on_progress=lambda p: job.__setitem__("progress", round(p, 3)))
                if body.subtitles and any(words.values()):
                    srt = out.with_suffix(".srt")
                    srt.write_text(build_srt(project, words), encoding="utf-8")
                    job["subtitles"] = str(srt)
                job["state"] = "done"
            except Exception as e:  # noqa: BLE001
                job.update(state="error", error=str(e)[-400:])

        threading.Thread(target=work, daemon=True).start()
        return {"job": job_id}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        if job_id not in st.jobs:
            raise HTTPException(404)
        return st.jobs[job_id]

    return app


def exit_when_parent_dies(pid: int) -> None:
    """Exit as soon as the app that started us is gone, even if it crashed.

    This waits on the parent's process handle. It deliberately does NOT block reading stdin:
    in the packaged build a pending stdin read froze FFmpeg and OpenCV whenever they touched
    the same handle.
    """
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
        os._exit(0)
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            os._exit(0)
        time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=str(Path.home() / ".clipchat"))
    args = ap.parse_args()
    token = os.environ.get("CLIPCHAT_TOKEN") or secrets.token_urlsafe(24)
    if "CLIPCHAT_TOKEN" not in os.environ:
        print(f"CLIPCHAT_TOKEN={token}", flush=True)
    if parent := os.environ.get("CLIPCHAT_PARENT_PID"):
        threading.Thread(target=exit_when_parent_dies, args=(int(parent),), daemon=True).start()
    app = create_app(Path(args.data_dir).expanduser(), token)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
