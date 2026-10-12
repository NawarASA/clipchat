"""Find the exciting moments in a video: the kills, the fights, the shouting.

Two signals, sampled on a 0.25s grid:
  * audio: loudness plus sudden *jumps* in loudness (impacts, ability sounds, announcer calls)
  * motion: how much of the picture changes between frames, and where (fights, explosions)

Each is turned into a robust z-score (median/MAD, so a constantly loud game doesn't drown
everything), blended into one excitement curve, smoothed, and its peaks become highlights.
The motion map also says *where* the action is, so zooms can aim at it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HOP = 0.25  # seconds per analysis step


@dataclass
class Analysis:
    t: np.ndarray  # step start times (source seconds)
    score: np.ndarray  # excitement, roughly z-score units
    cx: np.ndarray  # where on screen the motion is (0-1)
    cy: np.ndarray

    def to_dict(self) -> dict:
        return {k: [round(float(x), 3) for x in getattr(self, k)] for k in ("t", "score", "cx", "cy")}


@dataclass
class Moment:
    time: float  # source seconds of the peak
    score: float
    cx: float
    cy: float


def _robust_z(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    return (x - med) / (mad + 1e-6 if mad > 1e-6 else (np.std(x) + 1e-6))


def _smooth(x: np.ndarray, k: int = 3) -> np.ndarray:
    if len(x) < k:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def audio_curve(path: str, n: int) -> np.ndarray:
    """Per-step loudness (dB) plus its positive jumps, as a z-scored excitement signal."""
    import av

    rms = np.full(n, -90.0)
    try:
        with av.open(path) as c:
            if not c.streams.audio:
                return np.zeros(n)
            stream = c.streams.audio[0]
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            chunks = []
            for frame in c.decode(stream):
                for f in resampler.resample(frame):
                    chunks.append(f.to_ndarray().reshape(-1))
            if not chunks:
                return np.zeros(n)
            pcm = np.concatenate(chunks)
    except av.FFmpegError:
        return np.zeros(n)
    hop = int(16000 * HOP)
    for i in range(min(n, len(pcm) // hop)):
        seg = pcm[i * hop:(i + 1) * hop]
        rms[i] = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)
    onset = np.maximum(np.diff(rms, prepend=rms[0]), 0)  # sudden increases = hits
    return 0.5 * _robust_z(rms) + 0.5 * _robust_z(onset)


def motion_curve(path: str, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-step amount of on-screen change, and the centroid of that change."""
    import cv2

    amount, cx, cy = np.zeros(n), np.full(n, 0.5), np.full(n, 0.5)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return amount, cx, cy
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps * HOP))
    prev, i = None, 0
    while cap.grab():
        if i % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            small = cv2.cvtColor(cv2.resize(frame, (160, max(1, int(160 * h / w)))), cv2.COLOR_BGR2GRAY).astype(np.float32)
            k = i // step
            if prev is not None and k < n:
                diff = np.abs(small - prev)
                amount[k] = diff.mean()
                mask = diff > max(12.0, diff.mean() * 2)
                if mask.sum() > 20:  # enough changed pixels to locate the action
                    ys, xs = np.nonzero(mask)
                    cx[k], cy[k] = xs.mean() / small.shape[1], ys.mean() / small.shape[0]
            prev = small
        i += 1
    cap.release()
    return _robust_z(amount), cx, cy


def analyze(path: str, duration: float, has_audio: bool = True) -> Analysis:
    n = max(1, int(np.ceil(duration / HOP)))
    a = audio_curve(path, n) if has_audio else np.zeros(n)
    m, cx, cy = motion_curve(path, n)
    score = _smooth(0.6 * np.clip(a, -3, 8) + 0.4 * np.clip(m, -3, 8))
    # Ease the aim point so zooms target the area of sustained action, not one noisy frame.
    cx, cy = _smooth(cx, 5), _smooth(cy, 5)
    return Analysis(np.arange(n) * HOP, score, cx, cy)


def find_moments(a: Analysis, ranges: list[tuple[float, float]], min_gap: float = 4.0,
                 threshold: float = 1.0, limit: int = 12) -> list[Moment]:
    """Best peaks inside the given source ranges, at least ``min_gap`` seconds apart."""
    inside = np.zeros(len(a.t), dtype=bool)
    for s, e in ranges:
        inside |= (a.t >= s) & (a.t < e)
    candidates = [i for i in range(len(a.t)) if inside[i] and a.score[i] >= threshold
                  and a.score[i] >= a.score[max(0, i - 1)] and a.score[i] >= a.score[min(len(a.t) - 1, i + 1)]]
    picked: list[int] = []
    for i in sorted(candidates, key=lambda j: -a.score[j]):
        if all(abs(a.t[i] - a.t[j]) >= min_gap for j in picked):
            picked.append(i)
        if len(picked) >= limit:
            break
    return sorted((Moment(float(a.t[i] + HOP / 2), float(a.score[i]), float(a.cx[i]), float(a.cy[i])) for i in picked),
                  key=lambda m: m.time)
