import { Download, Moon, Plus, Redo2, Settings as SettingsIcon, SlidersHorizontal, Sparkles, Sun, Undo2 } from "lucide-react";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { SiriGlow } from "./Ambient.jsx";
import { api, connect } from "./api.js";
import Chat from "./Chat.jsx";
import ExportDialog from "./ExportDialog.jsx";
import Orb from "./Orb.jsx";
import Preview from "./Preview.jsx";
import Settings from "./Settings.jsx";
import StylePanel from "./StylePanel.jsx";
import Timeline from "./Timeline.jsx";

const load = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } };

function useTheme() {
  const [pref, setPref] = useState(() => load("clipchat.theme", "system"));
  const [textSize, setTextSize] = useState(() => load("clipchat.textsize", "normal"));
  const [systemDark, setSystemDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const on = (e) => setSystemDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const resolved = pref === "system" ? (systemDark ? "dark" : "light") : pref;
  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    window.clipchat.setTheme?.(resolved);
  }, [resolved]);
  useEffect(() => { document.documentElement.dataset.textsize = textSize; }, [textSize]);
  return {
    pref, resolved, textSize,
    setPref: (v) => { setPref(v); save("clipchat.theme", v); },
    setTextSize: (v) => { setTextSize(v); save("clipchat.textsize", v); },
  };
}

export default function App() {
  const [snap, setSnap] = useState(null);
  const [settings, setSettings] = useState(null);
  const [recipes, setRecipes] = useState([]);
  const [tab, setTab] = useState("chat");
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false); // the AI or a recipe is working
  const [toast, setToast] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [bootError, setBootError] = useState("");
  const theme = useTheme();
  const toastTimer = useRef();
  const dragDepth = useRef(0);

  const notify = useCallback((text, error = true) => {
    setToast({ text, error });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4500);
  }, []);

  const run = useCallback(async (fn) => {
    try {
      const s = await fn();
      if (s?.project) setSnap(s);
      if (s?.errors?.length) notify(s.errors.join("\n"));
      if (s?.result?.startsWith("Error")) notify(s.result.replace(/^Error: /, ""));
      return s;
    } catch (e) {
      notify(e.message);
    }
  }, [notify]);
  const edit = useCallback((tool, args) => run(() => api.edit(tool, args)), [run]);

  useEffect(() => {
    if (!window.clipchat.browser) document.body.classList.add("electron");
    connect()
      .then(async () => {
        setSnap(await api.project());
        setSettings(await api.settings());
        setRecipes(await api.recipes());
      })
      .catch((e) => setBootError(`Couldn't reach the editing engine: ${e.message}`));
  }, []);

  // Poll for preview progress and long-running status while anything is happening.
  const active = busy || snap?.preview?.rendering || !!snap?.status;
  useEffect(() => {
    if (!snap) return;
    const id = setInterval(async () => {
      try { setSnap(await api.project()); } catch { /* engine busy; next tick */ }
    }, active ? 500 : 2000);
    return () => clearInterval(id);
  }, [!!snap, active]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the selection valid as clips come and go.
  const clipCount = snap?.project.clips.length ?? 0;
  useEffect(() => { if (selected && selected > clipCount) setSelected(clipCount || null); }, [clipCount, selected]);

  // Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y (when not typing).
  useEffect(() => {
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (["TEXTAREA", "INPUT"].includes(document.activeElement?.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "z") { e.preventDefault(); run(e.shiftKey ? api.redo : api.undo); }
      if (k === "y") { e.preventDefault(); run(api.redo); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  const importPaths = async (paths) => {
    if (!paths.length) return;
    setBusy(true);
    try { await run(() => api.importFiles(paths)); } finally { setBusy(false); }
  };
  const pickFiles = async () => importPaths(await window.clipchat.openFiles());
  const runRecipe = async (key) => {
    setBusy(true);
    try {
      const r = await run(() => api.recipe(key));
      if (r) notify(`${recipes.find((x) => x.key === key)?.label} applied. Ctrl+Z to undo.`, false);
    } finally { setBusy(false); }
  };

  const drag = {
    onDragEnter: (e) => { if (e.dataTransfer.types.includes("Files")) { dragDepth.current++; setDragging(true); } },
    onDragLeave: () => { if (--dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false); } },
    onDragOver: (e) => e.preventDefault(),
    onDrop: (e) => {
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      importPaths([...e.dataTransfer.files].map((f) => window.clipchat.pathForFile(f)).filter(Boolean));
    },
  };

  const changeSettings = async (changes) => {
    try { setSettings(await api.saveSettings(changes)); } catch (e) { notify(e.message); }
  };

  if (!snap) {
    return (
      <div className="boot">
        <Orb size={72} state={bootError ? "idle" : "thinking"} />
        {bootError ? <p className="boot-error">{bootError}</p> : "Warming up…"}
      </div>
    );
  }

  const hasClips = snap.project.clips.length > 0;

  return (
    <div className="app" {...drag}>
      <SiriGlow active={busy} />
      <header className="topbar">
        <div className="island glass">
          <div className="brand"><Orb size={26} state={busy ? "thinking" : "idle"} />ClipChat</div>
        </div>
        <div className="island glass">
          <button className="pill pill-plain" onClick={pickFiles}><Plus size={17} />Add media</button>
          <button className="circle" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!snap.can_undo} onClick={() => run(api.undo)}><Undo2 size={18} /></button>
          <button className="circle" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!snap.can_redo} onClick={() => run(api.redo)}><Redo2 size={18} /></button>
        </div>
        <div className="spacer" />
        <div className="island glass">
          {settings && (
            <div className="seg" role="radiogroup" aria-label="AI model">
              <button role="radio" aria-checked={settings.provider === "claude"} className={settings.provider === "claude" ? "on" : ""}
                      onClick={() => changeSettings({ provider: "claude" })}>Claude</button>
              <button role="radio" aria-checked={settings.provider === "ollama"} className={settings.provider === "ollama" ? "on" : ""}
                      onClick={() => changeSettings({ provider: "ollama" })}>On-device</button>
            </div>
          )}
          <button className="circle" aria-label="Toggle dark mode" title="Light / dark"
                  onClick={() => theme.setPref(theme.resolved === "dark" ? "light" : "dark")}>
            {theme.resolved === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className="circle" aria-label="Settings" title="Settings" onClick={() => setShowSettings(true)}><SettingsIcon size={18} /></button>
        </div>
        <button className="pill pill-primary" style={{ height: "2.9rem", padding: "0 1.3rem" }} disabled={!hasClips} onClick={() => setShowExport(true)}>
          <Download size={17} />Export
        </button>
      </header>

      <main className="workspace">
        <section className="stage">
          {hasClips ? <Preview snap={snap} /> : <EmptyStage onPick={pickFiles} dragging={dragging} />}
          <Timeline snap={snap} run={run} edit={edit} selected={selected} setSelected={setSelected} onImport={pickFiles} />
        </section>

        <aside className="glass side">
          <div className="side-tabs">
            <div className="seg full" role="tablist">
              <button role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>
                <Sparkles size={15} />Assistant
              </button>
              <button role="tab" aria-selected={tab === "style"} className={tab === "style" ? "on" : ""} onClick={() => setTab("style")}>
                <SlidersHorizontal size={15} />Style
              </button>
            </div>
          </div>
          <div className="side-body" hidden={tab !== "chat"}>
            <Chat snap={snap} settings={settings} busy={busy} setBusy={setBusy} setSnap={setSnap} recipes={recipes}
                  runRecipe={runRecipe} onOpenSettings={() => setShowSettings(true)} notify={notify} />
          </div>
          <div className="side-body" hidden={tab !== "style"}>
            <StylePanel snap={snap} edit={edit} recipes={recipes} runRecipe={runRecipe} busy={busy}
                        selected={selected} setSelected={setSelected} />
          </div>
        </aside>
      </main>

      {dragging && hasClips && <div className="drop-veil glass">Drop to add to your video</div>}
      {toast && (
        <div className={`toast glass ${toast.error ? "error" : ""}`} role="status" onClick={() => setToast(null)}>{toast.text}</div>
      )}
      {showSettings && settings && (
        <Settings settings={settings} theme={theme} onSave={changeSettings} onClose={() => setShowSettings(false)} />
      )}
      {showExport && <ExportDialog snap={snap} onClose={() => setShowExport(false)} notify={notify} />}
    </div>
  );
}

function EmptyStage({ onPick, dragging }) {
  return (
    <div className={`welcome-stage glass ${dragging ? "drag" : ""}`}>
      <Orb size={84} state={dragging ? "listening" : "idle"} />
      <h1>Let's make something</h1>
      <p>Drop your videos, photos or music here. Then just say what you want, or tap a preset.</p>
      <button className="pill pill-primary" onClick={onPick}><Plus size={17} />Choose files</button>
      <div className="steps">
        <span className="step chip-glass"><b>1</b>Add your clips</span>
        <span className="step chip-glass"><b>2</b>Say what you want</span>
        <span className="step chip-glass"><b>3</b>Export & share</span>
      </div>
    </div>
  );
}
