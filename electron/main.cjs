// Electron main process: starts the editing engine, then shows the UI window.
const { app, BrowserWindow, dialog, ipcMain, nativeTheme, session, shell } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const net = require("net");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const BACKEND_DIR = path.join(ROOT, "backend");
const DEV = process.env.CLIPCHAT_DEV === "1";
const APP_ID = "com.clipchat.app";

let backend = null;
let backendReady = null; // Promise<{url, token}>
let backendLog = "";
let win = null;

// Window chrome colors per theme; the native min/max/close buttons sit on the app's own top bar.
const CHROME = {
  light: { bg: "#f3f1fa", overlay: { color: "#f3f1fa", symbolColor: "#1c1631", height: 54 } },
  dark: { bg: "#0f0d16", overlay: { color: "#0f0d16", symbolColor: "#f2f0fa", height: 54 } },
};

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

// The installed app runs the bundled engine; development runs the backend's virtualenv.
function engineCommand(port) {
  const common = ["--port", String(port), "--data-dir", app.getPath("userData")];
  if (app.isPackaged) {
    const bundle = path.join(process.resourcesPath, "engine-bundle");
    return {
      cmd: path.join(bundle, "engine", "clipchat-engine.exe"),
      args: common,
      cwd: path.join(bundle, "engine"),
      env: { CLIPCHAT_FFMPEG_DIR: path.join(bundle, "ffmpeg"), CLIPCHAT_WHISPER_DIR: path.join(bundle, "whisper-base") },
    };
  }
  const venv = process.platform === "win32"
    ? path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
    : path.join(BACKEND_DIR, ".venv", "bin", "python");
  return { cmd: fs.existsSync(venv) ? venv : "python", args: ["-m", "clipchat", ...common], cwd: BACKEND_DIR, env: {} };
}

async function startBackend() {
  const port = await freePort();
  // A fresh secret per launch: other programs and web pages can reach localhost, but not this.
  const token = crypto.randomBytes(24).toString("hex");
  const { cmd, args, cwd, env } = engineCommand(port);
  backend = spawn(cmd, args, {
    cwd,
    // The engine watches our PID and exits if we die. Its stdin stays disconnected on purpose:
    // a blocked stdin read inside the engine froze FFmpeg and OpenCV in the packaged build.
    env: { ...process.env, ...env, CLIPCHAT_TOKEN: token, CLIPCHAT_PARENT_PID: String(process.pid), PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  const keepLog = (chunk) => { backendLog = (backendLog + chunk.toString()).slice(-4000); };
  backend.stdout.on("data", keepLog);
  backend.stderr.on("data", keepLog);
  let exited = false;
  backend.on("error", (e) => { exited = true; keepLog(String(e)); });
  backend.on("exit", () => { exited = true; });

  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 300; i++) { // up to ~60s; a cold first launch unpacks a lot
    if (exited) break;
    try {
      const r = await fetch(`${url}/health`);
      if (r.ok) return { url, token };
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(backendLog || "The editing engine didn't start.");
}

function createWindow() {
  const chrome = CHROME[nativeTheme.shouldUseDarkColors ? "dark" : "light"];
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1000,
    minHeight: 640,
    show: false,
    backgroundColor: chrome.bg,
    titleBarStyle: "hidden",
    titleBarOverlay: chrome.overlay,
    autoHideMenuBar: true,
    title: "ClipChat",
    icon: app.isPackaged ? undefined : path.join(ROOT, "build", "icon.png"), // the installed .exe embeds it
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
    },
  });
  win.once("ready-to-show", () => win.show());
  // Dropping a file outside the drop zone must not navigate the window away.
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith("http://localhost:5173")) e.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  if (DEV) win.loadURL("http://localhost:5173");
  else win.loadFile(path.join(ROOT, "dist", "index.html"));
}

// The UI shows its "warming up" screen and waits here until the engine answers.
ipcMain.handle("backend-info", () => backendReady);

ipcMain.handle("set-theme", (_e, theme) => {
  const chrome = CHROME[theme === "dark" ? "dark" : "light"];
  if (win && process.platform !== "darwin") win.setTitleBarOverlay(chrome.overlay);
  win?.setBackgroundColor(chrome.bg);
});

ipcMain.handle("open-files", async () => {
  const r = await dialog.showOpenDialog(win, {
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "Videos, photos & music", extensions: ["mp4", "mov", "mkv", "webm", "avi", "m4v", "jpg", "jpeg", "png", "webp", "bmp",
        "mp3", "wav", "m4a", "aac", "flac", "ogg"] },
      { name: "All files", extensions: ["*"] },
    ],
  });
  return r.canceled ? [] : r.filePaths;
});

ipcMain.handle("save-file", async () => {
  const r = await dialog.showSaveDialog(win, {
    defaultPath: path.join(app.getPath("videos"), "my-video.mp4"),
    filters: [{ name: "MP4 video", extensions: ["mp4"] }],
  });
  return r.canceled ? null : r.filePath;
});

ipcMain.handle("show-in-folder", (_e, p) => shell.showItemInFolder(p));

// One ClipChat at a time: launching it again just brings the existing window forward.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.setAppUserModelId(APP_ID); // taskbar grouping and the app's name in Windows notifications

  app.whenReady().then(() => {
    // Only the microphone (for dictation) is ever granted to the page.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, cb, details) =>
      cb(permission === "media" && (details.mediaTypes || []).every((t) => t === "audio")));
    session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === "media");

    backendReady = startBackend();
    backendReady.catch((err) => {
      dialog.showErrorBox("ClipChat couldn't start",
        (app.isPackaged
          ? "The editing engine failed to start. Reinstalling ClipChat usually fixes this.\n\n"
          : "The Python backend failed to start. Did you run the setup steps in README.md?\n\n")
        + String(err.message).slice(-1500));
      app.quit();
    });
    createWindow();
  });

  app.on("window-all-closed", () => app.quit());
  app.on("will-quit", () => { if (backend) backend.kill(); });
}
