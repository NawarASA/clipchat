"""Bundles everything the installed app needs besides Electron into dist-engine/:

  engine/        clipchat-engine.exe + its Python runtime (PyInstaller)
  ffmpeg/        ffmpeg.exe (rendering)
  whisper-base/  the speech model, so captions work offline from the first run

Run with the backend venv:  backend/.venv/Scripts/python scripts/build_engine.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
OUT = ROOT / "dist-engine"
WORK = ROOT / ".build-tmp"


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def build_engine() -> None:
    step("Bundling the Python engine with PyInstaller")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(BACKEND / "engine.py"),
        "--name", "clipchat-engine", "--onedir", "--console", "--noconfirm", "--clean",
        "--distpath", str(OUT), "--workpath", str(WORK / "pyi"), "--specpath", str(WORK),
        "--paths", str(BACKEND),
        # Packages with data files or native libraries PyInstaller can't discover on its own.
        "--collect-all", "faster_whisper", "--collect-all", "ctranslate2", "--collect-all", "onnxruntime",
        "--collect-all", "av", "--collect-data", "cv2", "--collect-submodules", "uvicorn",
        "--exclude-module", "tkinter", "--exclude-module", "matplotlib", "--exclude-module", "pytest",
        "--exclude-module", "PyInstaller", "--exclude-module", "PIL",
    ], check=True, cwd=BACKEND)
    (OUT / "clipchat-engine").replace(OUT / "engine") if not (OUT / "engine").exists() else None


def copy_ffmpeg() -> None:
    step("Copying FFmpeg")
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg not found on PATH; install it first (winget install Gyan.FFmpeg).")
    (OUT / "ffmpeg").mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, OUT / "ffmpeg" / "ffmpeg.exe")


def copy_whisper() -> None:
    step("Fetching the Whisper 'base' model")
    from faster_whisper.utils import download_model
    target = OUT / "whisper-base"
    if target.exists():
        shutil.rmtree(target)
    download_model("base", output_dir=str(target))


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    build_engine()
    copy_ffmpeg()
    copy_whisper()
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    step(f"Done: {OUT} ({size:.0f} MB)")
