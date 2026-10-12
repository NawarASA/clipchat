"""The agent loops, with the network replaced by scripted fakes."""

from types import SimpleNamespace as NS

import httpx
import pytest
from conftest import make_project

from clipchat import llm
from clipchat.llm import ClaudeProvider, OllamaProvider, ProviderError
from clipchat.tools import Editor


class NoTranscriber:
    def is_cached(self, p):
        return True

    def words(self, p):
        return []


def _editor():
    return Editor(make_project(10), NoTranscriber())


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})  # snapshot; the live list keeps growing
        return self.responses.pop(0)


def _claude_with(responses):
    prov = ClaudeProvider(api_key="test")
    fake = FakeMessages(responses)
    prov.client = NS(beta=NS(messages=fake))
    return prov, fake


def test_claude_loop_executes_tools_and_returns_text():
    tool_turn = NS(stop_reason="tool_use", content=[
        NS(type="tool_use", id="t1", name="set_format", input={"aspect": "9:16", "fit": "blur"}),
        NS(type="tool_use", id="t2", name="trim_clip", input={"clip": 1, "trim_start": 1, "trim_end": 0}),
    ])
    final = NS(stop_reason="end_turn", content=[NS(type="text", text="Made it vertical and trimmed it.")])
    prov, fake = _claude_with([tool_turn, final])
    ed, events = _editor(), []
    reply = prov.chat(ed, "make it a tiktok and cut the first second", events.append)

    assert reply == "Made it vertical and trimmed it."
    assert ed.project.aspect == "9:16" and ed.project.clips[0].start == 1
    # Both results go back in ONE user message, matched by id.
    results = fake.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    first = fake.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"])
    assert "<project_state>" in first["messages"][0]["content"]
    assert [e["type"] for e in events] == ["tool_start", "tool_result"] * 2


def test_claude_refusal_and_auth_errors():
    prov, _ = _claude_with([NS(stop_reason="refusal", content=[])])
    assert "can't help" in prov.chat(_editor(), "x", lambda e: None)

    prov, fake = _claude_with([])

    def boom(**kw):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise llm.anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)

    fake.create = boom
    with pytest.raises(ProviderError, match="API key"):
        prov.chat(_editor(), "x", lambda e: None)


def test_ollama_loop(monkeypatch):
    replies = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "cut_section", "arguments": {"start": "0:02", "end": "4"}}}]}},
        {"message": {"role": "assistant", "content": "Cut 2 seconds."}},
    ]
    sent = []

    def fake_post(url, json, timeout):
        sent.append({**json, "messages": list(json["messages"])})
        return httpx.Response(200, json=replies.pop(0), request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    ed = _editor()
    reply = OllamaProvider().chat(ed, "cut 2 to 4", lambda e: None)
    assert reply == "Cut 2 seconds."
    assert ed.project.duration == pytest.approx(8)
    assert sent[1]["messages"][-1] == {"role": "tool", "tool_name": "cut_section",
                                       "content": sent[1]["messages"][-1]["content"]}
    assert sent[0]["tools"][0]["type"] == "function"


def test_ollama_not_running(monkeypatch):
    def refuse(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm.httpx, "post", refuse)
    with pytest.raises(ProviderError, match="isn't running"):
        OllamaProvider().chat(_editor(), "hi", lambda e: None)


def test_ollama_recovers_tool_calls_written_as_text(monkeypatch):
    replies = [
        {"message": {"role": "assistant", "content":
            'Sure! {"name": "set_format", "parameters": {"aspect": "1:1", "fit": "crop"}}'}},
        {"message": {"role": "assistant", "content":
            "```\n<project_state>\nTotal length: 0:10.0\n</project_state>\n```\nIt's square now."}},
    ]
    monkeypatch.setattr(llm.httpx, "post", lambda url, json, timeout: httpx.Response(
        200, json=replies.pop(0), request=httpx.Request("POST", url)))
    ed = _editor()
    reply = OllamaProvider().chat(ed, "make it square", lambda e: None)
    assert ed.project.aspect == "1:1"
    assert reply == "It's square now."


def test_ollama_history_is_trimmed_to_a_user_turn():
    prov = OllamaProvider()
    prov.messages += [{"role": "tool", "content": "x"}] * 50 + [{"role": "user", "content": "last"}]
    prov._trim()
    assert prov.messages[0]["role"] == "system" and prov.messages[1] == {"role": "user", "content": "last"}


def test_clean_reply_strips_state_dumps():
    dump = ("Current project state:\nTotal length: 0:13.5 | format: 9:16\nTimeline:\n"
            "  clip 1: talk.mp4 [0:00.0-0:01.4 of source] plays at 0:00.0-0:01.4\nMusic: none\n"
            "Look: cinematic\nAudio: voice cleanup on\nTransitions: none; fade in/out at ends: off\n")
    assert llm.clean_reply(dump) == ""
    assert llm.clean_reply("Done! It's vertical now.\n" + dump) == "Done! It's vertical now."
    assert llm.summarize_results(["Format set to 9:16 (blur).", "Error: x", "Music removed."]) == \
        "Format set to 9:16 (blur). Music removed."


def test_claude_asks_then_resumes_with_the_answer():
    ask_turn = NS(stop_reason="tool_use", content=[
        NS(type="tool_use", id="t1", name="set_format", input={"aspect": "9:16", "fit": "blur"}),
        NS(type="tool_use", id="t2", name="ask_user",
           input={"question": "Which song?", "options": ["beat.mp3", "chill.mp3"], "multi_select": False}),
    ])
    after = NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t3", name="set_speed", input={"clip": 1, "speed": 2})])
    final = NS(stop_reason="end_turn", content=[NS(type="text", text="Done: vertical and 2x.")])
    prov, fake = _claude_with([ask_turn, after, final])
    ed, events = _editor(), []
    assert prov.chat(ed, "make it vertical and add music", events.append) == "Which song?"
    assert ed.project.aspect == "9:16"  # the clear part was done before asking
    ask = next(e for e in events if e["type"] == "ask")
    assert ask == {"type": "ask", "question": "Which song?", "options": ["beat.mp3", "chill.mp3"], "multi": False}

    assert prov.chat(ed, "beat.mp3", events.append) == "Done: vertical and 2x."
    resumed = fake.calls[1]["messages"][-1]["content"]
    ids = [b.get("tool_use_id") for b in resumed if b.get("type") == "tool_result"]
    assert ids == ["t1", "t2"]  # every tool_use from the paused turn gets its result
    assert "The user answered: beat.mp3" in resumed[1]["content"]
    assert prov.pending is None


def test_ollama_asks_then_resumes(monkeypatch):
    replies = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "ask_user", "arguments": {"question": "How long?", "options": "15s, 30s, 60s", "multi_select": "false"}}}]}},
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "cut_section", "arguments": {"start": 5, "end": 10}}}]}},
        {"message": {"role": "assistant", "content": "Trimmed it down."}},
    ]
    sent = []

    def fake_post(url, json, timeout):
        sent.append({**json, "messages": list(json["messages"])})
        return httpx.Response(200, json=replies.pop(0), request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    prov, ed, events = OllamaProvider(), _editor(), []
    assert prov.chat(ed, "make it shorter", events.append) == "How long?"
    assert events[-1]["options"] == ["15s", "30s", "60s"] and events[-1]["multi"] is False
    assert prov.chat(ed, "15s", events.append) == "Trimmed it down."
    assert sent[1]["messages"][-1] == {"role": "tool", "tool_name": "ask_user", "content": "The user answered: 15s"}
    assert ed.project.duration == pytest.approx(5)


def test_ollama_replaces_offtopic_question_with_summary(monkeypatch):
    replies = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "set_format", "arguments": {"aspect": "9:16", "fit": "blur"}}}]}},
        {"message": {"role": "assistant", "content": "Want me to add music too?"}},
    ]
    monkeypatch.setattr(llm.httpx, "post", lambda url, json, timeout: httpx.Response(
        200, json=replies.pop(0), request=httpx.Request("POST", url)))
    reply = OllamaProvider().chat(_editor(), "make it vertical", lambda e: None)
    assert reply == "Format set to 9:16 (blur)."


def test_intent_hints_and_ask_validation():
    hints = llm.intent_hints("Make it a gaming TikTok with captions and remove the ums")
    assert any("9:16" in h for h in hints) and "auto_highlights" in hints and "set_captions" in hints
    assert "remove_filler_words" in hints
    assert llm.intent_hints("hello there") == []
    assert llm.parse_ask({"question": "Pick", "options": ["only one"]}) is None
    assert llm.parse_ask({"question": "Pick", "options": ["a", "b"], "multi_select": "yes"})["multi"] is True
