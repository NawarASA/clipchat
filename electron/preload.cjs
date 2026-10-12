// The only bridge between the UI and the OS. Keep it small.
const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("clipchat", {
  backend: () => ipcRenderer.invoke("backend-info"),
  openFiles: () => ipcRenderer.invoke("open-files"),
  saveFile: () => ipcRenderer.invoke("save-file"),
  showInFolder: (p) => ipcRenderer.invoke("show-in-folder", p),
  setTheme: (theme) => ipcRenderer.invoke("set-theme", theme),
  // Electron no longer exposes File.path; this is the supported replacement.
  pathForFile: (file) => webUtils.getPathForFile(file),
});
