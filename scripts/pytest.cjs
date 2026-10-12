// Runs the backend tests with the backend's own virtualenv, on any OS.
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const backend = path.join(__dirname, "..", "backend");
const venv = process.platform === "win32" ? [".venv", "Scripts", "python.exe"] : [".venv", "bin", "python"];
const py = path.join(backend, ...venv);
const r = spawnSync(fs.existsSync(py) ? py : "python", ["-m", "pytest", "-q", ...process.argv.slice(2)],
  { cwd: backend, stdio: "inherit" });
process.exit(r.status ?? 1);
