// Runs scripts/build_engine.py with the backend's virtualenv, on any OS.
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const venv = process.platform === "win32" ? [".venv", "Scripts", "python.exe"] : [".venv", "bin", "python"];
const py = path.join(root, "backend", ...venv);
if (!fs.existsSync(py)) {
  console.error("Set up the backend first (see README: Setup).");
  process.exit(1);
}
const r = spawnSync(py, [path.join(__dirname, "build_engine.py")], { cwd: root, stdio: "inherit" });
process.exit(r.status ?? 1);
