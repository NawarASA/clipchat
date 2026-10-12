"""Face tracking for smart reframing (e.g. landscape -> vertical that follows the speaker).

Samples a few frames per second, finds the most prominent face with OpenCV's
built-in detector (no model download), then smooths the track so the virtual
camera glides instead of jittering. The renderer turns the track into an
animated crop.
"""

from __future__ import annotations

import statistics
from typing import Callable

SAMPLE_FPS = 2.0
DETECT_WIDTH = 480  # detection runs on a downscaled frame; plenty for faces


def track_faces(path: str, on_progress: Callable[[float], None] | None = None) -> list[list[float]]:
    """Return [[t, cx, cy], ...] (cx/cy normalized 0-1), or [] if no faces were found."""
    import cv2  # heavy import, deferred

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, round(fps / SAMPLE_FPS))
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    raw: list[tuple[float, float, float] | None] = []
    times: list[float] = []
    prev: tuple[float, float] | None = None
    i = 0
    while True:
        if not cap.grab():
            break
        if i % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            scale = DETECT_WIDTH / w
            small = cv2.resize(frame, (DETECT_WIDTH, max(1, int(h * scale))))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                              minSize=(int(DETECT_WIDTH * 0.06),) * 2)
            sw, sh = small.shape[1], small.shape[0]
            faces = [f for f in faces if f[3] < 0.6 * sh]  # a "face" filling the frame is a false hit
            best = None
            if len(faces):
                # Prefer big faces, and faces near where the subject just was (avoids flicking
                # to a face in the background for one frame).
                def score(f):
                    x, y, fw, fh = f
                    cx, cy = (x + fw / 2) / sw, (y + fh / 2) / sh
                    dist = abs(cx - prev[0]) + abs(cy - prev[1]) if prev else 0
                    return fw * fh * (1.0 - min(dist, 0.8))
                x, y, fw, fh = max(faces, key=score)
                best = ((x + fw / 2) / sw, (y + fh / 2) / sh)
                prev = best
            raw.append(best)
            times.append(i / fps)
            if on_progress and total:
                on_progress(min(i / total, 1.0))
        i += 1
    cap.release()
    return smooth_track(times, raw)


def smooth_track(times: list[float], raw: list[tuple[float, float] | None]) -> list[list[float]]:
    """Fill gaps, remove outliers (median) and ease motion (exponential smoothing)."""
    found = [r for r in raw if r]
    # A real subject shows up in most samples; stray hits on patterns or textures don't.
    if len(found) < max(2, len(raw) * 0.3):
        return []
    filled, last = [], found[0]
    for r in raw:
        last = r or last
        filled.append(last)
    k = 2
    med = [(statistics.median(p[0] for p in filled[max(0, j - k):j + k + 1]),
            statistics.median(p[1] for p in filled[max(0, j - k):j + k + 1])) for j in range(len(filled))]
    out, sx, sy = [], med[0][0], med[0][1]
    for t, (x, y) in zip(times, med):
        sx += 0.35 * (x - sx)
        sy += 0.35 * (y - sy)
        out.append([round(t, 2), round(sx, 4), round(sy, 4)])
    return out


def keyframes(track: list[list[float]], start: float, end: float, axis: int,
              tolerance: float = 0.015, max_keys: int = 40) -> list[tuple[float, float]]:
    """Clip-relative (t, value) keyframes for one axis, thinned to what actually moves."""
    pts = [(t - start, p[axis]) for p in track if start - 0.6 <= (t := p[0]) <= end + 0.6]
    if not pts:
        return []
    keys = [pts[0]]
    for t, v in pts[1:]:
        if abs(v - keys[-1][1]) >= tolerance:
            keys.append((t, v))
    if keys[-1] != pts[-1]:
        keys.append(pts[-1])
    if len(keys) > max_keys:  # evenly thin very long tracks
        stride = len(keys) / max_keys
        keys = [keys[int(i * stride)] for i in range(max_keys)] + [keys[-1]]
    return keys


def lerp_expr(keys: list[tuple[float, float]]) -> str:
    """FFmpeg expression of t that linearly interpolates between keyframes."""
    if len(keys) == 1:
        return f"{keys[0][1]:.4f}"
    expr = f"{keys[-1][1]:.4f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        if t1 - t0 < 1e-3:
            continue
        expr = f"if(lt(t,{t1:.3f}),{v0:.4f}+({v1 - v0:.4f})*(t-{t0:.3f})/{t1 - t0:.3f},{expr})"
    return f"if(lt(t,{keys[0][0]:.3f}),{keys[0][1]:.4f},{expr})"
