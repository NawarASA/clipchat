"""The project timeline: an ordered list of clip segments plus global settings.

Every edit the AI makes is a pure operation on this structure. Nothing touches
video pixels until render time, which keeps edits instant and undoable.

Times are in seconds. A clip's ``start``/``end`` are positions in its *source*
media; its position in the *output* is the sum of the (speed-adjusted) clips
before it.
"""

from __future__ import annotations

import copy
import dataclasses
import itertools
from dataclasses import asdict, dataclass, field

ASPECTS = ("original", "9:16", "1:1", "16:9")
FITS = ("smart", "blur", "crop", "pad")
CAPTION_STYLES = ("classic", "bold", "karaoke", "minimal")
POSITIONS = ("top", "center", "bottom")
LOOKS = ("none", "warm", "cool", "vivid", "cinematic", "vintage", "black & white")
TRANSITIONS = ("none", "fade")
MIN_CLIP = 0.05  # anything shorter than this is dropped rather than kept as a sliver
PHOTO_SECONDS = 4.0


class EditError(ValueError):
    """An edit that can't be applied; the message is shown to the AI/user."""


@dataclass
class Media:
    id: str
    path: str
    name: str
    kind: str  # "video" | "audio" | "image"
    duration: float
    width: int = 0
    height: int = 0
    has_audio: bool = True
    # Face track for smart reframing: [[source_time, center_x, center_y], ...] normalized 0-1.
    # None = not analyzed yet; [] = analyzed, no faces found.
    focus: list[list[float]] | None = None


@dataclass
class Clip:
    id: str
    media_id: str
    start: float
    end: float
    speed: float = 1.0
    zoom: float = 1.0
    # Animated punch-ins: [[t0, t1, amount, cx, cy, flash], ...] in SOURCE seconds, aim point in
    # source-frame 0-1 coords. Source time means they survive cuts and splits untouched; each piece
    # just renders the events that fall inside it. Lists are replaced, never mutated in place.
    zooms: list[list[float]] = field(default_factory=list)

    @property
    def length(self) -> float:
        """Source seconds this clip uses."""
        return self.end - self.start

    @property
    def out_length(self) -> float:
        """Seconds this clip lasts in the finished video."""
        return self.length / self.speed


@dataclass
class TextOverlay:
    text: str
    start: float  # output time
    end: float
    position: str = "top"


@dataclass
class Captions:
    enabled: bool = False
    style: str = "classic"
    position: str = "bottom"


@dataclass
class Music:
    media_id: str
    volume: float = 0.25
    duck: bool = True  # dip the music automatically whenever someone talks


@dataclass
class Look:
    name: str = "none"
    brightness: float = 0.0  # -1..1, 0 = unchanged
    contrast: float = 0.0
    saturation: float = 0.0


@dataclass
class Audio:
    clean_voice: bool = False  # denoise + speech EQ + gentle compression
    voice_volume: float = 1.0
    normalize: bool = True  # even out loudness to social-media level (-14 LUFS)


@dataclass
class Project:
    media: dict[str, Media] = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)
    aspect: str = "original"
    fit: str = "blur"
    captions: Captions = field(default_factory=Captions)
    music: Music | None = None
    texts: list[TextOverlay] = field(default_factory=list)
    look: Look = field(default_factory=Look)
    audio: Audio = field(default_factory=Audio)
    transition: str = "none"
    fade_ends: bool = False
    next_id: int = 1

    # ---- serialization -------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            media={k: Media(**v) for k, v in d.get("media", {}).items()},
            clips=[Clip(**c) for c in d.get("clips", [])],
            aspect=d.get("aspect", "original"),
            fit=d.get("fit", "blur"),
            captions=Captions(**d.get("captions", {})),
            music=Music(**d["music"]) if d.get("music") else None,
            texts=[TextOverlay(**t) for t in d.get("texts", [])],
            look=Look(**d.get("look", {})),
            audio=Audio(**d.get("audio", {})),
            transition=d.get("transition", "none"),
            fade_ends=d.get("fade_ends", False),
            next_id=d.get("next_id", 1),
        )

    # ---- helpers -------------------------------------------------------

    def new_id(self, prefix: str) -> str:
        i = self.next_id
        self.next_id += 1
        return f"{prefix}{i}"

    @property
    def duration(self) -> float:
        return sum(c.out_length for c in self.clips)

    def clip_at(self, number: int) -> Clip:
        """Clips are addressed 1-based, the way people talk about them."""
        if not 1 <= number <= len(self.clips):
            raise EditError(f"There is no clip {number}; the timeline has {len(self.clips)} clip(s).")
        return self.clips[number - 1]

    def offsets(self) -> list[float]:
        """Output start time of each clip."""
        return list(itertools.accumulate((c.out_length for c in self.clips), initial=0.0))[:-1]

    def find_media(self, name: str) -> Media:
        name_l = name.lower().strip()
        for m in self.media.values():
            if m.id == name or m.name.lower() == name_l:
                return m
        matches = [m for m in self.media.values() if name_l in m.name.lower()]
        if len(matches) == 1:
            return matches[0]
        raise EditError(f"No imported file matches '{name}'. Imported: {', '.join(m.name for m in self.media.values()) or 'none'}.")

    def summary(self) -> str:
        """Compact, human-readable state handed to the AI on every turn."""
        lines = [f"Total length: {fmt(self.duration)} | format: {self.aspect} (fit: {self.fit})"]
        if self.clips:
            lines.append("Timeline:")
            for n, (c, off) in enumerate(zip(self.clips, self.offsets()), 1):
                m = self.media[c.media_id]
                src = "photo" if m.kind == "image" else f"{fmt(c.start)}-{fmt(c.end)} of source"
                n_z = sum(1 for z in c.zooms if z[1] > c.start and z[0] < c.end)
                extra = "".join([f", {c.speed:g}x speed" if c.speed != 1 else "",
                                 f", zoom {c.zoom:g}x" if c.zoom != 1 else "",
                                 f", {n_z} highlight zoom(s)" if n_z else ""])
                lines.append(f"  clip {n}: {m.name} [{src}{extra}] plays at {fmt(off)}-{fmt(off + c.out_length)}")
        else:
            lines.append("Timeline: empty")
        lib = [f"{m.name} ({m.kind}{'' if m.kind == 'image' else ', ' + fmt(m.duration)})" for m in self.media.values()]
        lines.append("Imported files: " + (", ".join(lib) if lib else "none"))
        cap = self.captions
        lines.append(f"Captions: {'on, ' + cap.style + ', ' + cap.position if cap.enabled else 'off'}")
        if self.music:
            lines.append(f"Music: {self.media[self.music.media_id].name} at {round(self.music.volume * 100)}% volume"
                         f"{', ducks under speech' if self.music.duck else ''}")
        else:
            lines.append("Music: none")
        lk = self.look
        adj = [f"{k} {getattr(lk, k):+.1f}" for k in ("brightness", "contrast", "saturation") if getattr(lk, k)]
        lines.append(f"Look: {lk.name}{' (' + ', '.join(adj) + ')' if adj else ''}")
        a = self.audio
        lines.append(f"Audio: voice cleanup {'on' if a.clean_voice else 'off'}, voice volume {round(a.voice_volume * 100)}%, "
                     f"loudness levelling {'on' if a.normalize else 'off'}")
        lines.append(f"Transitions: {self.transition}; fade in/out at ends: {'on' if self.fade_ends else 'off'}")
        for i, t in enumerate(self.texts, 1):
            lines.append(f"Text {i}: \"{t.text}\" {fmt(t.start)}-{fmt(t.end)} ({t.position})")
        return "\n".join(lines)


def fmt(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


# ---- range math ---------------------------------------------------------


def subtract_ranges(start: float, end: float, remove: list[tuple[float, float]],
                    min_len: float = MIN_CLIP) -> list[tuple[float, float]]:
    """Return the parts of [start, end] not covered by ``remove``."""
    keep: list[tuple[float, float]] = []
    cursor = start
    for r0, r1 in sorted(remove):
        if r1 <= cursor or r0 >= end:
            continue
        if r0 > cursor:
            keep.append((cursor, r0))
        cursor = max(cursor, r1)
    if cursor < end:
        keep.append((cursor, end))
    return [(a, b) for a, b in keep if b - a >= min_len]


def _split(project: Project, c: Clip, pieces: list[tuple[float, float]]) -> list[Clip]:
    """Replace a clip by pieces of itself, keeping its speed/zoom."""
    return [dataclasses.replace(c, id=c.id if i == 0 else project.new_id("c"), start=a, end=b)
            for i, (a, b) in enumerate(pieces)]


def remove_source_ranges(project: Project, ranges_by_media: dict[str, list[tuple[float, float]]],
                         min_len: float = 0.25) -> float:
    """Cut source-time ranges out of every clip. Returns output seconds removed.

    Leftover pieces shorter than ``min_len`` are dropped: an automatic cut that
    leaves a 0.1s blip between two pauses looks like a glitch, not an edit.
    """
    before = project.duration
    new_clips: list[Clip] = []
    for c in project.clips:
        if project.media[c.media_id].kind == "image":
            new_clips.append(c)  # photos have no audio to cut
            continue
        new_clips += _split(project, c, subtract_ranges(c.start, c.end, ranges_by_media.get(c.media_id, []), min_len))
    project.clips = new_clips
    return before - project.duration


def cut_output_range(project: Project, start: float, end: float) -> float:
    """Remove [start, end] measured on the output timeline, across clips."""
    if end <= start:
        raise EditError("The end of the cut must be after its start.")
    total = project.duration
    if start >= total:
        raise EditError(f"The video is only {fmt(total)} long.")
    before = total
    new_clips: list[Clip] = []
    for c, off in zip(project.clips, project.offsets()):
        a, b = max(start, off), min(end, off + c.out_length)
        remove = [(c.start + (a - off) * c.speed, c.start + (b - off) * c.speed)] if b > a else []
        new_clips += _split(project, c, subtract_ranges(c.start, c.end, remove))
    project.clips = new_clips
    _shift_texts(project, start, end)
    return before - project.duration


def keep_only_output(project: Project, keep: list[tuple[float, float]]) -> float:
    """Keep only the given output-time ranges; everything else is cut."""
    total = project.duration
    keep = sorted((max(a, 0.0), min(b, total)) for a, b in keep if b > a)
    if not keep:
        raise EditError("Give at least one section to keep.")
    gaps, cursor = [], 0.0
    for a, b in keep:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < total:
        gaps.append((cursor, total))
    before = project.duration
    for a, b in reversed(gaps):  # back to front, so earlier times don't shift
        if b - a > 0.01:
            cut_output_range(project, a, b)
    return before - project.duration


def _shift_texts(project: Project, start: float, end: float) -> None:
    """Keep text overlays attached to the footage they were placed over."""
    gap = end - start
    kept = []
    for t in project.texts:
        if t.end <= start:
            kept.append(t)
        elif t.start >= end:
            kept.append(TextOverlay(t.text, t.start - gap, t.end - gap, t.position))
        else:
            s = min(t.start, start)
            e = max(start, t.end - gap)
            if e - s >= 0.3:
                kept.append(TextOverlay(t.text, s, e, t.position))
    project.texts = kept


class History:
    """Snapshot-based undo/redo. Projects are small, so full copies are fine."""

    def __init__(self, limit: int = 50):
        self.undo_stack: list[Project] = []
        self.redo_stack: list[Project] = []
        self.limit = limit

    def push(self, project: Project) -> None:
        self.undo_stack.append(copy.deepcopy(project))
        del self.undo_stack[: -self.limit]
        self.redo_stack.clear()

    def undo(self, current: Project) -> Project:
        if not self.undo_stack:
            raise EditError("Nothing to undo.")
        self.redo_stack.append(copy.deepcopy(current))
        return self.undo_stack.pop()

    def redo(self, current: Project) -> Project:
        if not self.redo_stack:
            raise EditError("Nothing to redo.")
        self.undo_stack.append(copy.deepcopy(current))
        return self.redo_stack.pop()
