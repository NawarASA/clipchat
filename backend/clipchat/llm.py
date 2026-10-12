"""Chat providers: Claude (Anthropic API) and local models (Ollama).

Both run the same loop: send the conversation + tool list, execute any tool
calls against the Editor, feed results back, repeat until the model answers in
plain text. Each provider keeps its own message history in its native format.

Besides the editing tools, the model gets ``ask_user``: when a request is
genuinely ambiguous it can pause and show the user a short multiple-choice
question (rendered as tick boxes). The user's answer resumes the same turn.
"""

from __future__ import annotations

import json
import re
from typing import Callable

import anthropic
import httpx

from .tools import TOOLS, Editor

MAX_STEPS = 10

SYSTEM = """You are ClipChat, the assistant inside a desktop video editor for people who have never edited video.
The user describes what they want in plain words; you make it happen by calling the editing tools.

Fit the request exactly:
- Do every part of what the user asked, and nothing they didn't. Don't add music, captions, looks, zooms or cuts on your own initiative. If a request has several parts, handle each one.
- Read the project state that comes with every message (clips, times, settings, imported files). Use it; never invent clips or files.
- Times you pass to tools are plain seconds (75 = 1:15). "Output time" means where something plays in the finished edit.
- Clip numbers shift after cuts, splits and moves; account for that when making several edits in one turn.
- To cut by content ("remove the part where I talk about X"), call get_transcript or find_in_transcript first, then cut_section with the timestamps you found.
- If a tool returns an error, fix the call or explain the problem in one sentence.

Map common requests:
- "TikTok/Reels/Shorts/vertical" -> set_format 9:16: fit smart for a person talking to camera, fit blur for gameplay, screen recordings or scenery (never smart for games).
- "subtitles/captions" -> set_captions (karaoke is the popular TikTok style). "dead air/pauses" -> remove_silences. "ums and uhs" -> remove_filler_words.
- "background noise/echo/bad audio" -> set_audio clean_voice. "cinematic/warmer/brighter/black and white" -> set_look. "speed up/slow motion" -> set_speed.
- "smooth transitions/fade" -> set_transitions. "shorter version/best parts" of a talk -> get_transcript, then keep_only.
- gaming/sports/action ("zoom on the kills", "make it hype", "highlight reel") -> auto_highlights (style reel cuts down to the best moments, zoom keeps everything). "zoom in at 0:05" -> add_zoom.
- A "gaming TikTok/Short/Reel" means BOTH: set_format 9:16 with fit blur, then auto_highlights.
- Files must be imported by the user (Add media button or drag and drop). If they mention music or a file that isn't imported, say so instead of guessing.

When to ask (ask_user):
- Ask only if the request is genuinely ambiguous AND the answer would change the result a lot. Examples: several music files are imported and they said "add music"; they want it "shorter" without a length and the video is long; one request could reasonably mean two very different edits.
- Never ask about things with a sensible default, never ask more than one question at a time, and never ask again right after the user answered. If you can act, act.
- Give 2-5 short options, most likely first. Use multi_select when several options can apply at once (e.g. which extras to add).
- Do the parts that are clear first, then ask about the unclear part.

Reply style: one or two short, friendly sentences saying exactly what changed. No markdown headings, no jargon (say "pauses", not "silence threshold")."""

ASK_TOOL = {
    "name": "ask_user",
    "description": "Pause and ask the user one short multiple-choice question when their request is genuinely "
                   "ambiguous. They see the options as tick boxes; their answer comes back as this tool's result.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One short, plain question."},
            "options": {"type": "array", "items": {"type": "string"}, "description": "2-5 short options, most likely first."},
            "multi_select": {"type": "boolean", "description": "True if the user may pick several options."},
        },
        "required": ["question", "options", "multi_select"],
        "additionalProperties": False,
    },
}

# Keyword hints for small local models, which follow instructions far better when the request is
# pre-mapped to the right tool. (Claude doesn't need these.)
_HINTS = [
    (r"\b(tik ?tok|reels?|shorts?|vertical|portrait)\b", "make it 9:16 with set_format (fit blur for gameplay/screen footage, smart for a person talking)"),
    (r"\b(square|instagram post)\b", "set_format 1:1"),
    (r"\b(youtube|widescreen|landscape|16:9)\b", "set_format 16:9"),
    (r"\b(caption|subtitle)", "set_captions"),
    (r"\b(pause|silen|dead air)", "remove_silences"),
    (r"\b(ums?|uhs?|filler)\b", "remove_filler_words"),
    (r"\b(noise|echo|hum|hiss|audio quality|clean.{0,10}audio|clear(er)? (voice|audio|sound))\b", "set_audio with clean_voice true"),
    (r"\b(music|song|soundtrack)\b", "set_music, only with an imported audio file"),
    (r"\b(gaming|game|gameplay|kills?|highlights?|hype|best (moments|parts|plays))\b", "auto_highlights"),
    (r"\b(slow ?mo|slow motion|speed up|faster|slower)\b", "set_speed"),
    (r"\bzoom\b", "add_zoom for a specific time, auto_highlights for 'the action'"),
    (r"\b(cinematic|warm|cool|vintage|black and white|b&w|brighter|darker|colou?rs?)\b", "set_look"),
    (r"\b(fade|transition)", "set_transitions"),
    (r"\b(title|text overlay|write)\b", "add_text"),
]


def intent_hints(text: str) -> list[str]:
    low = text.lower()
    return [hint for pattern, hint in _HINTS if re.search(pattern, low)]


def _with_state(editor: Editor, text: str) -> str:
    return f"<project_state>\n{editor.project.summary()}\n</project_state>\n\n{text}"


_STATE_ECHO = re.compile(r"(```\w*\s*)?<project_state>.*?</project_state>(\s*```)?", re.S)
# Lines that look like Project.summary() output, which small models like to "report" back.
_STATE_LINE = re.compile(r"^\s*(Current project state:|Total length:|Timeline:|clip \d+:|Imported files:|"
                         r"Captions:|Music:|Look:|Audio:|Transitions:|Text( \d+)?:|```\w*$).*$", re.M)
_TOOL_NAMES = {t["name"] for t in TOOLS} | {ASK_TOOL["name"]}


def clean_reply(text: str) -> str:
    """Small models sometimes parrot the state block back; never show it to the user."""
    text = _STATE_LINE.sub("", _STATE_ECHO.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def summarize_results(results: list[str]) -> str:
    """Fallback reply built from what the tools actually did."""
    firsts = [r.split("\n")[0] for r in results if not r.startswith("Error")]
    return " ".join(firsts) or "Done."


def tool_calls_in_text(text: str) -> list[tuple[str, dict]]:
    """Find tool calls a model wrote as JSON text instead of actually calling the tool.

    Llama-family models do this regularly: '{"name": "remove_silences", "parameters": {...}}'.
    """
    found, dec, i = [], json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("name") in _TOOL_NAMES:
            args = obj.get("parameters", obj.get("arguments", {}))
            found.append((obj["name"], args if isinstance(args, dict) else {}))
        i = end
    return found


def parse_ask(args: dict) -> dict | None:
    """Validate an ask_user call (small models send loose shapes). None if unusable."""
    question = str(args.get("question") or "").strip()
    options = args.get("options") or []
    if isinstance(options, str):
        options = [o.strip() for o in re.split(r"[,\n;]", options)]
    options = [str(o).strip() for o in options if str(o).strip()][:6]
    if not question or len(options) < 2:
        return None
    multi = args.get("multi_select")
    multi = multi if isinstance(multi, bool) else str(multi).lower() in ("true", "yes", "1")
    return {"question": question, "options": options, "multi": multi}


Emit = Callable[[dict], None]


class ProviderError(Exception):
    """A user-facing problem talking to the model (bad key, model not running...)."""


def _run_tool(editor: Editor, name: str, args: dict, emit: Emit) -> str:
    emit({"type": "tool_start", "name": name, "args": args})
    result = editor.execute(name, args)
    emit({"type": "tool_result", "name": name, "result": result,
          "ok": not result.startswith("Error"), "version": editor.version})
    return result


def _answer_text(text: str) -> str:
    return f"The user answered: {text}"


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str = "claude-opus-5-5", api_key: str | None = None):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.messages: list[dict] = []
        # Results owed to the API for the turn that paused on ask_user: [tool_result, ...] + the ask's id.
        self.pending: dict | None = None
        # strict: the API guarantees tool inputs match the schema exactly.
        self.tools = [{**t, "strict": True} for t in [*TOOLS, ASK_TOOL]]

    def chat(self, editor: Editor, text: str, emit: Emit) -> str:
        if self.pending:
            # The user just answered our question: it becomes the ask_user tool result.
            content = [*self.pending["results"],
                       {"type": "tool_result", "tool_use_id": self.pending["ask_id"], "content": _answer_text(text)},
                       {"type": "text", "text": _with_state(editor, "(Continue with the request using that answer.)")}]
            self.pending = None
            self.messages.append({"role": "user", "content": content})
        else:
            self.messages.append({"role": "user", "content": _with_state(editor, text)})
        for _ in range(MAX_STEPS):
            try:
                resp = self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=SYSTEM,
                    tools=self.tools,
                    messages=self.messages,
                    output_config={"effort": "medium"},
                    cache_control={"type": "ephemeral"},
                    # If a safety classifier declines, retry on a fallback model in the same call.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.AuthenticationError:
                raise ProviderError("Claude rejected the API key. Add a valid key in Settings "
                                    "(or set ANTHROPIC_API_KEY) and try again.") from None
            except anthropic.PermissionDeniedError as e:
                raise ProviderError(f"Your API key can't use {self.model}: {e.message}") from None
            except anthropic.NotFoundError:
                raise ProviderError(f"Model '{self.model}' wasn't found. Check the model name in Settings.") from None
            except anthropic.RateLimitError:
                raise ProviderError("Claude is rate-limiting requests right now. Wait a moment and try again.") from None
            except anthropic.APIStatusError as e:
                raise ProviderError(f"Claude API error {e.status_code}: {e.message}") from None
            except anthropic.APIConnectionError:
                raise ProviderError("Couldn't reach the Claude API. Check your internet connection.") from None

            # Keep the full content (including thinking blocks) so history stays append-only.
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "refusal":
                return "Sorry, I can't help with that one."
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "tool_use" and tool_uses:
                asks = [b for b in tool_uses if b.name == ASK_TOOL["name"]]
                results = [{"type": "tool_result", "tool_use_id": b.id,
                            "content": _run_tool(editor, b.name, b.input, emit)} for b in tool_uses if b not in asks]
                if asks and (ask := parse_ask(asks[0].input)):
                    # Every tool_use needs a result; extra asks get a polite "one at a time".
                    results += [{"type": "tool_result", "tool_use_id": b.id, "content": "Ask one question at a time.",
                                 "is_error": True} for b in asks[1:]]
                    self.pending = {"ask_id": asks[0].id, "results": results}
                    emit({"type": "ask", **ask})
                    return ask["question"]
                results += [{"type": "tool_result", "tool_use_id": b.id, "content": "Error: the question needs "
                             "a question and at least two options.", "is_error": True} for b in asks]
                self.messages.append({"role": "user", "content": results})
                continue
            if resp.stop_reason == "max_tokens":
                return "That reply ran too long and was cut off - try asking for one change at a time."
            return clean_reply("".join(b.text for b in resp.content if b.type == "text")) or "Done."
        return "I stopped after several steps. Check the preview and tell me what to adjust."


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str = "llama3.1:8b", host: str = "http://127.0.0.1:11434"):
        self.model = model
        self.host = host.rstrip("/")
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM}]
        self.pending = False  # the last turn ended on ask_user
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in [*TOOLS, ASK_TOOL]]

    MAX_HISTORY = 40  # local context windows are small; keep the system prompt + recent turns
    STATE_PREFIX = "Current project state:\n"

    def _trim(self) -> None:
        if len(self.messages) <= self.MAX_HISTORY:
            return
        rest = self.messages[-self.MAX_HISTORY:]
        while rest and rest[0]["role"] != "user":  # never start on an orphaned tool result
            rest.pop(0)
        self.messages = [self.messages[0], *rest]

    def chat(self, editor: Editor, text: str, emit: Emit) -> str:
        results: list[str] = []
        # State goes in its own system message: inside the user turn, small models echo it back.
        # Only the latest state is kept, so stale clip numbers can't confuse the model.
        self.messages = [m for m in self.messages if not (m.get("content") or "").startswith(self.STATE_PREFIX)]
        self.messages.append({"role": "system", "content": self.STATE_PREFIX + editor.project.summary()})
        if self.pending:
            self.pending = False
            self.messages.append({"role": "tool", "tool_name": ASK_TOOL["name"], "content": _answer_text(text)})
        else:
            hints = intent_hints(text)
            self.messages.append({"role": "user", "content": text + (
                f"\n\n(Tools that fit this request: {'; '.join(hints)}.)" if hints else "")})
        self._trim()
        for _ in range(MAX_STEPS):
            try:
                r = httpx.post(f"{self.host}/api/chat", timeout=300, json={
                    "model": self.model, "messages": self.messages, "tools": self.tools, "stream": False,
                    # Tool schemas alone are ~3K tokens; Ollama's default window is too small.
                    "options": {"temperature": 0.2, "num_ctx": 16384},
                })
            except httpx.ConnectError:
                raise ProviderError("Ollama isn't running. Start it (open the Ollama app or run "
                                    "`ollama serve`) and try again.") from None
            except httpx.TimeoutException:
                raise ProviderError("The local model took too long to answer.") from None
            if r.status_code == 404:
                raise ProviderError(f"Local model '{self.model}' isn't installed. Run `ollama pull {self.model}`.")
            if r.status_code >= 400:
                detail = r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
                if "does not support tools" in str(detail):
                    raise ProviderError(f"'{self.model}' can't call tools. Pick a tool-capable model such as "
                                        "llama3.1:8b or qwen2.5 in Settings.")
                raise ProviderError(f"Ollama error: {detail}")
            msg = r.json()["message"]
            self.messages.append(msg)
            content = msg.get("content") or ""
            calls = [(c.get("function", {}).get("name", ""), c.get("function", {}).get("arguments"))
                     for c in msg.get("tool_calls") or []]
            if not calls:
                calls = tool_calls_in_text(content)
            if not calls:
                reply = clean_reply(content)
                # Small models often end with an unrequested question instead of saying what they did;
                # show the factual summary instead.
                if results and (not reply or reply.endswith("?")):
                    return summarize_results(results)
                return reply or summarize_results(results)
            ask = None
            for name, args in calls:
                args = args if isinstance(args, dict) else {}
                if name == ASK_TOOL["name"]:
                    ask = ask or parse_ask(args)
                    continue
                result = _run_tool(editor, name, args, emit)
                results.append(result)
                self.messages.append({"role": "tool", "tool_name": name, "content": result})
            if ask:
                self.pending = True
                emit({"type": "ask", **ask})
                return ask["question"]
        return "I stopped after several steps. Check the preview and tell me what to adjust."


def list_ollama_models(host: str) -> list[str]:
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=3)
        return sorted(m["name"] for m in r.json().get("models", []))
    except (httpx.HTTPError, ValueError):
        return []
