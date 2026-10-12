import pytest
from fastapi.testclient import TestClient

from clipchat.server import create_app

H = {"X-ClipChat-Token": "t0k"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path, "t0k"))


def test_token_required(client):
    assert client.get("/health").status_code == 200
    assert client.get("/project").status_code == 401
    assert client.get("/project", headers={"X-ClipChat-Token": "wrong"}).status_code == 401
    assert client.get("/project?token=t0k").status_code == 200


def test_import_edit_undo_flow(client, sample_video, sample_music):
    r = client.post("/import", headers=H, json={"paths": [str(sample_video), str(sample_music), "C:/nope.mp4"]})
    body = r.json()
    assert r.status_code == 200
    assert len(body["project"]["clips"]) == 1 and len(body["project"]["media"]) == 2
    assert len(body["errors"]) == 1  # the missing file

    r = client.post("/edit", headers=H, json={"tool": "trim_clip", "args": {"clip": 1, "trim_start": 1, "trim_end": 0}})
    assert r.json()["duration"] == pytest.approx(5, abs=0.1)
    r = client.post("/undo", headers=H)
    assert r.json()["duration"] == pytest.approx(6, abs=0.1)
    assert client.post("/redo", headers=H).json()["duration"] == pytest.approx(5, abs=0.1)


def test_settings_hide_api_key(client):
    r = client.post("/settings", headers=H, json={"provider": "ollama", "api_key": "sk-secret"})
    assert r.status_code == 200
    s = client.get("/settings", headers=H).json()
    assert s["provider"] == "ollama" and s["has_api_key"] and "api_key" not in s
    assert client.post("/settings", headers=H, json={"provider": "gpt"}).status_code == 400


def test_engine_exits_when_parent_dies(tmp_path):
    """The engine must notice the app disappearing (even a crash) without touching stdin."""
    import subprocess
    import sys
    import time
    from pathlib import Path
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child = subprocess.Popen(
        [sys.executable, "-c", f"from clipchat.server import exit_when_parent_dies; exit_when_parent_dies({parent.pid})"],
        stdin=subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parent.parent))
    time.sleep(1.5)
    assert child.poll() is None  # parent alive: keep running
    parent.kill()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        raise AssertionError("engine kept running after its parent died")


def test_chat_asks_first_then_sends_combined_message(client, sample_video, monkeypatch):
    seen = []

    class FakeProvider:
        name, model, pending = "ollama", "fake", False

        def chat(self, editor, text, emit):
            seen.append(text)
            return "ok"

    import json
    st = client.app.state.clipchat
    client.post("/import", headers=H, json={"paths": [str(sample_video)]})
    monkeypatch.setattr(st, "get_provider", lambda: FakeProvider())
    lines = [json.loads(x) for x in client.post("/chat", headers=H, json={"message": "make it look nicer"}).text.splitlines()]
    assert any(e["type"] == "ask" and e["multi"] for e in lines) and not seen  # asked, model untouched
    client.post("/chat", headers=H, json={"message": "Richer colors"})
    assert len(seen) == 1 and "make it look nicer" in seen[0] and "Richer colors" in seen[0]
