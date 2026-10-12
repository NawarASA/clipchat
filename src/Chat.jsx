import { ArrowUp, Check, LoaderCircle, Mic, Square, X } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { api, chat } from "./api.js";
import Orb from "./Orb.jsx";
import RecipeIcon from "./RecipeIcon.jsx";

const SUGGESTIONS = [
  "Remove the awkward pauses",
  "Cut out the ums and uhs",
  "Make it a TikTok with captions",
  "Clean up the background noise",
  "Zoom in on the best moments",
  "Make a 30 second highlight reel",
];

const TOOL_LABEL = {
  trim_clip: "Trimmed", cut_section: "Cut a section", keep_only: "Picked highlights", split_clip: "Split",
  delete_clip: "Deleted clip", move_clip: "Reordered", add_clip: "Added clip", set_speed: "Speed",
  set_zoom: "Zoom", set_photo_length: "Photo length", remove_silences: "Removed pauses",
  remove_filler_words: "Removed filler words", get_transcript: "Read the transcript",
  find_in_transcript: "Searched the transcript", set_captions: "Captions", set_format: "Format",
  set_look: "Color look", set_audio: "Audio", set_music: "Music", set_transitions: "Transitions",
  add_text: "Added text", remove_text: "Removed text", undo: "Undo",
  auto_highlights: "Found the highlights", add_zoom: "Zoom", remove_zooms: "Removed zooms",
};

export default function Chat({ snap, settings, busy, setBusy, setSnap, recipes, runRecipe, onOpenSettings, notify }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("");
  const scroller = useRef();
  const textarea = useRef();
  const hasClips = snap.project.clips.length > 0;
  const needsKey = settings?.provider === "claude" && !settings.has_api_key;

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages, status]);

  // Grow the box with its content.
  useEffect(() => {
    const t = textarea.current;
    if (t) { t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 128)}px`; }
  }, [input]);

  const updateLast = (fn) => setMessages((ms) => [...ms.slice(0, -1), fn(ms[ms.length - 1])]);

  const send = async (text) => {
    text = text.trim();
    if (!text || busy) return;
    setMessages((ms) => ms.map((m) => (m.ask && !m.ask.answered ? { ...m, ask: { ...m.ask, answered: text } } : m)));
    setInput("");
    setBusy(true);
    setStatus("Thinking…");
    setMessages((ms) => [...ms, { role: "user", text }, { role: "ai", text: "", actions: [], pending: true }]);
    try {
      await chat(text, (ev) => {
        if (ev.type === "tool_start") {
          setStatus(`${TOOL_LABEL[ev.name] || ev.name}…`);
          updateLast((m) => ({ ...m, actions: [...m.actions, { name: ev.name, running: true }] }));
        } else if (ev.type === "tool_result") {
          setStatus("Thinking…");
          updateLast((m) => {
            const actions = [...m.actions];
            const i = actions.findLastIndex((a) => a.running && a.name === ev.name);
            if (i >= 0) actions[i] = { name: ev.name, ok: ev.ok, result: ev.result };
            return { ...m, actions };
          });
        } else if (ev.type === "status") {
          setStatus(ev.text);
        } else if (ev.type === "ask") {
          updateLast((m) => ({ ...m, ask: { question: ev.question, options: ev.options, multi: ev.multi } }));
        } else if (ev.type === "reply") {
          updateLast((m) => ({ ...m, text: ev.text, pending: false }));
        } else if (ev.type === "error") {
          updateLast((m) => ({ ...m, text: ev.text, error: true, pending: false }));
        } else if (ev.type === "done") {
          setSnap(ev.snapshot);
        }
      });
    } catch (e) {
      updateLast((m) => ({ ...m, text: e.message, error: true, pending: false }));
    } finally {
      setBusy(false);
      setStatus("");
    }
  };

  const clear = async () => {
    await api.resetChat();
    setMessages([]);
  };

  return (
    <>
      <div className="messages" ref={scroller} aria-live="polite">
        {messages.length === 0 ? (
          <div className="hello">
            <Orb size={92} state={busy ? "thinking" : "idle"} />
            <h2>What are we making?</h2>
            <p>{hasClips ? "Tell me in your own words. I'll do the editing." : "Add a video first, then tell me what you want."}</p>
            <div className="bubbles">
              {SUGGESTIONS.map((s, i) => (
                <button key={s} className="bubble-btn chip-glass" style={{ animationDelay: `${i * 45}ms` }}
                        disabled={!hasClips || busy || needsKey} onClick={() => send(s)}>{s}</button>
              ))}
            </div>
            {recipes.length > 0 && (
              <>
                <div className="label">Or one tap, no typing</div>
                <div className="bubbles">
                  {recipes.slice(0, 4).map((r, i) => (
                    <button key={r.key} className="bubble-btn chip-glass" style={{ animationDelay: `${(i + 6) * 45}ms` }}
                            disabled={!hasClips || busy} onClick={() => runRecipe(r.key)} title={r.description}>
                      <RecipeIcon name={r.key} size={12} small />{r.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button className="pill pill-plain" style={{ height: "2rem", fontSize: "0.78rem" }} onClick={clear} disabled={busy}>New chat</button>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role} ${m.error ? "error" : ""}`}>
            {m.actions?.length > 0 && (
              <div className="actions">
                {m.actions.map((a, j) => (
                  <span key={j} className={`action chip-glass ${a.running ? "" : a.ok ? "ok" : "fail"}`} title={a.result}>
                    <span className="dot">
                      {a.running ? <LoaderCircle size={11} className="spin" /> : a.ok ? <Check size={11} strokeWidth={3.5} /> : <X size={11} strokeWidth={3.5} />}
                    </span>
                    {TOOL_LABEL[a.name] || a.name}
                  </span>
                ))}
              </div>
            )}
            {m.text && <div className={`msg-bubble ${m.role === "ai" ? "chip-glass" : ""}`}>{m.text}</div>}
            {m.ask && <AskCard ask={m.ask} disabled={busy} onAnswer={send} />}
            {m.pending && !m.text && (
              <div className="thinking"><Orb size={30} state="thinking" />{status || "Thinking…"}</div>
            )}
          </div>
        ))}
      </div>

      {needsKey && (
        <div className="notice chip-glass">
          Claude needs an API key. <button onClick={onOpenSettings}>Add one</button>, or switch to <b>On-device</b> at the top.
        </div>
      )}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <div className="composer-box">
          <MicButton disabled={busy} onText={(t) => setInput((v) => (v ? `${v} ${t}` : t))} notify={notify} />
          <textarea
            ref={textarea}
            value={input}
            rows={1}
            aria-label="Message"
            placeholder={hasClips ? "Ask for any edit…" : "Add a video to get started"}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            disabled={busy}
          />
          <button className="send-btn" aria-label="Send" disabled={busy || !input.trim()}><ArrowUp size={19} strokeWidth={2.5} /></button>
        </div>
      </form>
    </>
  );
}

// A clarifying question: tap one option, or tick several and press Continue.
function AskCard({ ask, disabled, onAnswer }) {
  const [picked, setPicked] = useState([]);
  const done = Boolean(ask.answered);
  const toggle = (o) => setPicked((p) => (p.includes(o) ? p.filter((x) => x !== o) : [...p, o]));
  return (
    <div className={`ask-card ${done ? "done" : ""}`} role="group" aria-label={ask.question}>
      {ask.options.map((o) => {
        const on = done ? ask.answered.split(", ").includes(o) : picked.includes(o);
        return (
          <button key={o} type="button" className={`ask-option ${on ? "on" : ""}`} disabled={disabled || done}
                  role={ask.multi ? "checkbox" : "radio"} aria-checked={on}
                  onClick={() => (ask.multi ? toggle(o) : onAnswer(o))}>
            <span className={`ask-mark ${ask.multi ? "box" : "dot"}`}>{on && <Check size={12} strokeWidth={3.5} />}</span>
            {o}
          </button>
        );
      })}
      {!done && ask.multi && (
        <button type="button" className="pill pill-primary ask-go" disabled={disabled || !picked.length}
                onClick={() => onAnswer(ask.options.filter((o) => picked.includes(o)).join(", "))}>
          Continue
        </button>
      )}
      {!done && <div className="ask-hint">Or type your own answer below.</div>}
    </div>
  );
}

// Tap the orb to talk, tap again to stop. The orb swells with your voice; Whisper transcribes locally.
function MicButton({ disabled, onText, notify }) {
  const [state, setState] = useState("idle"); // idle | recording | working
  const rec = useRef(null);
  const orb = useRef(null);

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      const r = new MediaRecorder(stream);
      // Live level meter: drives the orb's --lvl directly, no React re-renders.
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      let raf = 0;
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += ((v - 128) / 128) ** 2;
        orb.current?.style.setProperty("--lvl", Math.min(Math.sqrt(sum / buf.length) * 4, 1).toFixed(3));
        raf = requestAnimationFrame(tick);
      };
      tick();
      r.ondataavailable = (e) => chunks.push(e.data);
      r.onstop = async () => {
        cancelAnimationFrame(raf);
        orb.current?.style.setProperty("--lvl", "0");
        ctx.close();
        stream.getTracks().forEach((t) => t.stop());
        setState("working");
        try {
          const { text } = await api.dictate(new Blob(chunks, { type: r.mimeType }));
          if (text) onText(text); else notify("I didn't catch that. Try again a little closer to the mic.");
        } catch (e) {
          notify(e.message);
        } finally {
          setState("idle");
        }
      };
      r.start();
      rec.current = r;
      setState("recording");
    } catch {
      notify("Couldn't use the microphone. Check that one is connected and allowed.");
    }
  };

  return (
    <button
      type="button"
      className="mic-orb"
      aria-label={state === "recording" ? "Stop dictating" : "Speak instead of typing"}
      title={state === "recording" ? "Tap to stop" : "Tap and speak"}
      disabled={disabled || state === "working"}
      onClick={() => (state === "recording" ? rec.current?.stop() : start())}
    >
      <Orb ref={orb} size={34} state={state === "recording" ? "listening" : state === "working" ? "thinking" : "idle"} />
      {state === "idle" && <Mic size={15} className="mic-icon" />}
      {state === "recording" && <Square size={11} fill="#fff" className="mic-icon" />}
    </button>
  );
}
