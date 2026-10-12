"""Speech-to-text with word timestamps, via faster-whisper (runs locally, no API).

Transcripts are cached on disk keyed by file path + size + mtime, so each file
is only transcribed once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

FILLERS = {"um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm", "mhm"}
# Multi-word fillers are matched on consecutive words.
FILLER_PHRASES = [("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of")]


@dataclass
class Word:
    text: str
    start: float
    end: float

    @property
    def norm(self) -> str:
        return re.sub(r"[^\w']", "", self.text.lower())


class Transcriber:
    def __init__(self, cache_dir: Path, model_size: str = "base"):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_size = model_size
        self._model = None
        self._lock = threading.Lock()
        self._mem: dict[str, list[Word]] = {}

    def _key(self, path: str) -> str:
        st = Path(path).stat()
        raw = f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}|{self.model_size}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def is_cached(self, path: str) -> bool:
        key = self._key(path)
        return key in self._mem or (self.cache_dir / f"{key}.json").exists()

    def words(self, path: str) -> list[Word]:
        key = self._key(path)
        if key in self._mem:
            return self._mem[key]
        cache = self.cache_dir / f"{key}.json"
        if cache.exists():
            words = [Word(**w) for w in json.loads(cache.read_text(encoding="utf-8"))]
        else:
            words = self._transcribe(path)
            cache.write_text(json.dumps([asdict(w) for w in words]), encoding="utf-8")
        self._mem[key] = words
        return words

    def _transcribe(self, path: str) -> list[Word]:
        with self._lock:  # one whisper job at a time; the model isn't cheap to run twice
            if self._model is None:
                self._model = self._load("auto")
            try:
                return self._run(path)
            except RuntimeError as e:
                # "auto" picks CUDA whenever an NVIDIA GPU exists, even if the CUDA
                # libraries (cuBLAS/cuDNN) aren't installed. Fall back to the CPU.
                if not any(k in str(e).lower() for k in ("cuda", "cublas", "cudnn")):
                    raise
                self._model = self._load("cpu")
                return self._run(path)

    def text(self, path: str) -> str:
        """Plain transcription of a short recording (voice dictation). Not cached."""
        with self._lock:
            if self._model is None:
                self._model = self._load("auto")
            try:
                segments, _ = self._model.transcribe(path, vad_filter=True)
                return " ".join(s.text.strip() for s in segments).strip()
            except RuntimeError as e:
                if not any(k in str(e).lower() for k in ("cuda", "cublas", "cudnn")):
                    raise
                self._model = self._load("cpu")
                segments, _ = self._model.transcribe(path, vad_filter=True)
                return " ".join(s.text.strip() for s in segments).strip()

    def _load(self, device: str):
        from faster_whisper import WhisperModel  # heavy import, deferred
        # The installed app ships the model so captions work offline from the first run.
        model = os.environ.get("CLIPCHAT_WHISPER_DIR") or self.model_size
        return WhisperModel(model, device=device, compute_type="int8")

    def _run(self, path: str) -> list[Word]:
        # Whisper normally "cleans up" disfluencies; a prompt full of them makes it keep
        # the ums and uhs, which filler-word removal depends on.
        segments, _ = self._model.transcribe(path, word_timestamps=True, vad_filter=True,
                                             initial_prompt="Umm, so, uh, let me think, like, hmm... Okay, um, here's what I mean.")
        return [Word(w.word.strip(), float(w.start), float(w.end))
                for seg in segments for w in (seg.words or []) if w.word.strip()]


def filler_ranges(words: list[Word], extra: list[str] | None = None) -> list[tuple[float, float]]:
    """Source-time ranges covering filler words (and phrases like 'you know')."""
    singles = FILLERS | {e.lower() for e in (extra or []) if " " not in e}
    phrases = FILLER_PHRASES + [tuple(e.lower().split()) for e in (extra or []) if " " in e]
    norms = [w.norm for w in words]
    hits: list[tuple[float, float]] = []
    i = 0
    while i < len(words):
        matched = next((p for p in phrases if tuple(norms[i:i + len(p)]) == p), None)
        if matched:
            hits.append((words[i].start, words[i + len(matched) - 1].end))
            i += len(matched)
        elif norms[i] in singles:
            hits.append((words[i].start, words[i].end))
            i += 1
        else:
            i += 1
    return hits


def search(words: list[Word], query: str, context: int = 6) -> list[dict]:
    """Find every place an exact phrase is spoken (case and punctuation ignored)."""
    q = [re.sub(r"[^\w']", "", t.lower()) for t in query.split()]
    q = [t for t in q if t]
    if not q:
        return []
    norms = [w.norm for w in words]
    results = []
    for i in range(len(words) - len(q) + 1):
        if norms[i:i + len(q)] == q:
            lo, hi = max(0, i - context), min(len(words), i + len(q) + context)
            results.append({"start": words[i].start, "end": words[i + len(q) - 1].end,
                            "context": " ".join(w.text for w in words[lo:hi])})
    return results
