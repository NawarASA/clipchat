"""Inspecting media (PyAV) and running FFmpeg."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .timeline import PHOTO_SECONDS, Media

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# On Windows, stop every ffmpeg call from flashing a console window.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ffmpeg_bin(name: str = "ffmpeg") -> str:
    # The installed app ships its own FFmpeg; development uses the one on PATH.
    bundled = os.environ.get("CLIPCHAT_FFMPEG_DIR")
    if bundled and (exe := Path(bundled) / f"{name}.exe").exists():
        return str(exe)
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} was not found on PATH. Install FFmpeg (https://ffmpeg.org) and restart ClipChat.")
    return path


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    # stdin=DEVNULL matters: the engine keeps a blocking read on its own stdin (to notice the app
    # closing), and on Windows an inherited handle with a pending read freezes FFmpeg's key polling.
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW, **kw)


def probe(path: str, media_id: str) -> Media:
    import av  # ships with faster-whisper; saves bundling a second 200MB FFmpeg tool

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    try:
        container = av.open(str(p))
    except av.FFmpegError as e:
        raise ValueError(f"Couldn't read {p.name}: {e}") from None
    with container:
        videos = [s for s in container.streams.video if not _is_cover_art(s)]
        has_audio = bool(container.streams.audio)
        duration = container.duration / 1e6 if container.duration else 0.0
        if not videos and not has_audio:
            raise ValueError(f"{p.name} has no video or audio.")
        width = height = rotation = 0
        if videos:
            width, height = videos[0].codec_context.width, videos[0].codec_context.height
            try:  # the display rotation is only reported on decoded frames
                rotation = abs(int(next(container.decode(videos[0])).rotation or 0)) % 360
            except (StopIteration, av.FFmpegError, AttributeError):
                rotation = 0
    if rotation in (90, 270):
        width, height = height, width
    if p.suffix.lower() in IMAGE_EXTS:
        return Media(id=media_id, path=str(p.resolve()), name=p.name, kind="image", duration=PHOTO_SECONDS,
                     width=width, height=height, has_audio=False, focus=[])
    kind = "audio" if not videos or p.suffix.lower() in AUDIO_EXTS else "video"
    return Media(id=media_id, path=str(p.resolve()), name=p.name, kind=kind, duration=duration,
                 width=width, height=height, has_audio=has_audio)


def _is_cover_art(stream) -> bool:
    """MP3s often carry album art as a one-frame 'video' stream."""
    import av
    try:
        return bool(stream.disposition & av.stream.Disposition.attached_pic)
    except AttributeError:
        return False


def thumbnail(media: Media, out: Path) -> Path:
    if out.exists():
        return out
    seek = [] if media.kind == "image" else ["-ss", f"{min(media.duration * 0.25, 3.0):.2f}"]  # seeking a still yields nothing
    run([ffmpeg_bin(), "-y", "-nostdin", *seek, "-i", media.path, "-frames:v", "1", "-vf", "scale=240:-2", str(out)])
    return out


_SILENCE_RE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def detect_silences(path: str, threshold_db: float = -35.0, min_silence: float = 0.6) -> list[tuple[float, float]]:
    """Return (start, end) source-time ranges where the audio is quiet."""
    out = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-nostdin", "-i", path, "-vn",
               "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}", "-f", "null", "-"])
    ranges: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in _SILENCE_RE.findall(out.stderr):
        if kind == "start":
            start = max(float(value), 0.0)
        elif start is not None:
            ranges.append((start, float(value)))
            start = None
    if start is not None:  # silent through to the end of the file
        ranges.append((start, float("inf")))
    return ranges


def shrink(ranges: list[tuple[float, float]], padding: float) -> list[tuple[float, float]]:
    """Leave a little breathing room on each side of a cut so speech isn't clipped."""
    out = []
    for a, b in ranges:
        a2 = a + padding if a > 0 else a
        b2 = b - padding
        if b2 - a2 > 0.05:
            out.append((a2, b2))
    return out
