// Tiny client for the local Python backend.
let base = "";
let token = "";

// Outside Electron (UI development in a normal browser), take the backend address
// and token from the URL: http://localhost:5173/?backend=http://127.0.0.1:8765&token=...
if (!window.clipchat) {
  const q = new URLSearchParams(location.search);
  window.clipchat = {
    browser: true,
    backend: async () => ({ url: q.get("backend"), token: q.get("token") }),
    openFiles: async () => (prompt("Full path of a file to import:") || "").split(";").filter(Boolean),
    saveFile: async () => prompt("Export to (full path):", "C:\\my-video.mp4"),
    showInFolder: async () => {},
    pathForFile: () => null,
    setTheme: () => {},
  };
}

export async function connect() {
  const info = await window.clipchat.backend();
  base = info.url;
  token = info.token;
}

export const fmt = (s) => {
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
};

export const mediaUrl = (path) => `${base}${path}${path.includes("?") ? "&" : "?"}token=${token}`;

async function call(method, path, body, raw) {
  const r = await fetch(base + path, {
    method,
    headers: raw ? { "X-ClipChat-Token": token } : { "Content-Type": "application/json", "X-ClipChat-Token": token },
    body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

export const api = {
  project: () => call("GET", "/project"),
  importFiles: (paths) => call("POST", "/import", { paths }),
  edit: (tool, args) => call("POST", "/edit", { tool, args }),
  undo: () => call("POST", "/undo"),
  redo: () => call("POST", "/redo"),
  newProject: () => call("POST", "/new"),
  recipes: () => call("GET", "/recipes"),
  recipe: (key) => call("POST", `/recipe/${key}`),
  settings: () => call("GET", "/settings"),
  saveSettings: (s) => call("POST", "/settings", s),
  resetChat: () => call("POST", "/chat/reset"),
  exportTo: (opts) => call("POST", "/export", opts),
  job: (id) => call("GET", `/jobs/${id}`),
  dictate: (blob) => call("POST", "/dictate", undefined, blob),
};

// Streams newline-delimited JSON events from the agent as it works.
export async function chat(message, onEvent) {
  const r = await fetch(base + "/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-ClipChat-Token": token },
    body: JSON.stringify({ message }),
  });
  if (!r.ok) throw new Error(`Chat failed (${r.status})`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}
