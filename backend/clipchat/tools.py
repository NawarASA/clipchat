"""The editing tools the AI can call, the one-click recipes, and the Editor that runs them.

Tool schemas are written once in JSON Schema and adapted per provider
(see ``llm.py``). Every tool returns a short plain-text result that is fed
back to the model, so it can confirm what happened to the user. The UI's
buttons call the very same tools, so chat and clicks always agree.
"""

from __future__ import annotations

import copy
import dataclasses
import re
from typing import Any, Callable

from . import media as media_mod
from . import highlights, reframe
from .render import place_words
from .timeline import (ASPECTS, CAPTION_STYLES, FITS, LOOKS, POSITIONS, TRANSITIONS, Clip, EditError, History,
                       Music, Project, TextOverlay, cut_output_range, fmt, keep_only_output, remove_source_ranges)
from .transcribe import Transcriber, Word, filler_ranges, search


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_SEC = "Seconds. Plain number, e.g. 12.5."
_CLIP = {"type": "integer", "description": "Clip number on the timeline, starting at 1."}
_SLIDER = {"type": "number", "description": "-1 to 1; 0 = unchanged."}

TOOLS: list[dict] = [
    {"name": "trim_clip",
     "description": "Shave time off the beginning and/or end of one clip. Use for 'cut the first 3 seconds of clip 2'.",
     "input_schema": _obj({
         "clip": _CLIP,
         "trim_start": {"type": "number", "description": "Seconds to remove from the clip's start (0 for none)."},
         "trim_end": {"type": "number", "description": "Seconds to remove from the clip's end (0 for none)."},
     }, ["clip", "trim_start", "trim_end"])},
    {"name": "cut_section",
     "description": "Delete a stretch of the finished video, given in output time (where it plays in the edit). "
                    "Works across clip boundaries. Use for 'cut from 0:10 to 0:15'.",
     "input_schema": _obj({"start": {"type": "number", "description": _SEC},
                           "end": {"type": "number", "description": _SEC}}, ["start", "end"])},
    {"name": "keep_only",
     "description": "Keep only the listed sections of the edit (output time) and cut everything else. Use to make "
                    "highlights or a shorter version, e.g. 'make a 30 second version with the best parts' "
                    "(read the transcript first to choose sections).",
     "input_schema": _obj({"sections": {"type": "array", "items": _obj(
         {"start": {"type": "number"}, "end": {"type": "number"}}, ["start", "end"])}}, ["sections"])},
    {"name": "split_clip",
     "description": "Split one clip into two at a point, measured in seconds from the start of that clip.",
     "input_schema": _obj({"clip": _CLIP, "at": {"type": "number", "description": _SEC}}, ["clip", "at"])},
    {"name": "delete_clip", "description": "Remove a whole clip from the timeline.",
     "input_schema": _obj({"clip": _CLIP}, ["clip"])},
    {"name": "move_clip", "description": "Reorder: move a clip so it becomes clip number `to_position`.",
     "input_schema": _obj({"clip": _CLIP, "to_position": {"type": "integer"}}, ["clip", "to_position"])},
    {"name": "add_clip",
     "description": "Append an imported video or photo to the end of the timeline.",
     "input_schema": _obj({"file": {"type": "string", "description": "Imported file name."}}, ["file"])},
    {"name": "set_speed",
     "description": "Speed up or slow down a clip (or every clip). 2 = twice as fast, 0.5 = slow motion.",
     "input_schema": _obj({"clip": {"type": "integer", "description": "Clip number, or 0 for every clip."},
                           "speed": {"type": "number", "description": "0.25 to 4. 1 = normal."}},
                          ["clip", "speed"])},
    {"name": "set_zoom",
     "description": "Punch in (zoom) on a clip for emphasis. 1 = no zoom, 1.3 = noticeable punch-in.",
     "input_schema": _obj({"clip": {"type": "integer", "description": "Clip number, or 0 for every clip."},
                           "zoom": {"type": "number", "description": "1 to 2."}}, ["clip", "zoom"])},
    {"name": "set_photo_length", "description": "Set how long a photo stays on screen.",
     "input_schema": _obj({"clip": _CLIP, "seconds": {"type": "number"}}, ["clip", "seconds"])},
    {"name": "remove_silences",
     "description": "Automatically cut quiet pauses / dead air from every clip.",
     "input_schema": _obj({
         "min_pause": {"type": "number", "description": "Only cut pauses at least this long, in seconds. "
                                                        "0.6 is a good default; 1.0+ is gentle; 0.3 is aggressive."},
         "threshold_db": {"type": "number", "description": "Loudness below which audio counts as silent. "
                                                           "Default -35; use -30 for noisy rooms."},
     }, ["min_pause", "threshold_db"])},
    {"name": "remove_filler_words",
     "description": "Cut filler words like um, uh, erm, 'you know' using the speech transcript.",
     "input_schema": _obj({"extra_words": {"type": "array", "items": {"type": "string"},
                                           "description": "Additional words/phrases to cut, e.g. ['like', 'basically']. "
                                                          "Empty list for just the standard fillers."}},
                          ["extra_words"])},
    {"name": "get_transcript",
     "description": "Read what is said in the edit, with output timestamps. Use before cutting by content "
                    "(e.g. 'remove the part where I mention pricing') or choosing highlights.",
     "input_schema": _obj({})},
    {"name": "find_in_transcript",
     "description": "Find where an exact word or phrase is spoken in the edit; returns output timestamps.",
     "input_schema": _obj({"phrase": {"type": "string"}}, ["phrase"])},
    {"name": "set_captions", "description": "Turn auto-generated captions (subtitles from the speech) on or off and style them.",
     "input_schema": _obj({
         "enabled": {"type": "boolean"},
         "style": {"type": "string", "enum": list(CAPTION_STYLES),
                   "description": "classic = white with outline; bold = big yellow; karaoke = words light up as "
                                  "they're spoken (TikTok style); minimal = small with a dark box."},
         "position": {"type": "string", "enum": list(POSITIONS)},
     }, ["enabled", "style", "position"])},
    {"name": "set_format",
     "description": "Set the output shape. 9:16 for TikTok/Reels/Shorts, 1:1 for square posts, 16:9 for YouTube, "
                    "original to keep the source shape.",
     "input_schema": _obj({
         "aspect": {"type": "string", "enum": list(ASPECTS)},
         "fit": {"type": "string", "enum": list(FITS),
                 "description": "How footage fills a different shape: smart = follow the person's face (only for "
                                "people talking to camera); blur = whole frame on a blurred background (best for "
                                "gameplay, screen recordings, landscapes); crop = zoom to fill the center; pad = black bars."},
     }, ["aspect", "fit"])},
    {"name": "set_look",
     "description": "Color grade the whole video with a filter, and/or adjust brightness, contrast and saturation.",
     "input_schema": _obj({"look": {"type": "string", "enum": list(LOOKS)},
                           "brightness": _SLIDER, "contrast": _SLIDER, "saturation": _SLIDER},
                          ["look", "brightness", "contrast", "saturation"])},
    {"name": "set_audio",
     "description": "Improve the speech audio: voice cleanup removes background noise/hum and makes voices clearer; "
                    "loudness levelling makes it as loud as other social videos.",
     "input_schema": _obj({"clean_voice": {"type": "boolean"},
                           "voice_volume": {"type": "number", "description": "0 to 2. 1 = unchanged, 0 = mute."},
                           "normalize": {"type": "boolean"}},
                          ["clean_voice", "voice_volume", "normalize"])},
    {"name": "set_music",
     "description": "Put an imported audio file under the whole video as background music, or remove music.",
     "input_schema": _obj({
         "file": {"type": "string", "description": "Imported audio file name, or 'none' to remove music."},
         "volume": {"type": "number", "description": "0 to 1. 0.2-0.3 sits under speech; 0.8+ for music-only videos."},
         "duck": {"type": "boolean", "description": "Automatically lower the music while someone is talking."},
     }, ["file", "volume", "duck"])},
    {"name": "set_transitions",
     "description": "Soft fade between clips, and/or fade in from black at the start and out at the end.",
     "input_schema": _obj({"between_clips": {"type": "string", "enum": list(TRANSITIONS)},
                           "fade_in_out": {"type": "boolean"}}, ["between_clips", "fade_in_out"])},
    {"name": "add_text",
     "description": "Show a text overlay (title, label, call to action) between two output times.",
     "input_schema": _obj({
         "text": {"type": "string"},
         "start": {"type": "number", "description": _SEC},
         "end": {"type": "number", "description": _SEC},
         "position": {"type": "string", "enum": list(POSITIONS)},
     }, ["text", "start", "end", "position"])},
    {"name": "remove_text", "description": "Remove a text overlay by its number (1-based).",
     "input_schema": _obj({"index": {"type": "integer"}}, ["index"])},
    {"name": "auto_highlights",
     "description": "Automatically find the most exciting moments (kills, fights, big plays, loud reactions) "
                    "and edit around them: smooth punch-in zooms aimed at the action, and optionally cut the "
                    "video down to just those moments. Use for gaming clips, sports, 'make it hype', 'zoom on the kills'.",
     "input_schema": _obj({
         "style": {"type": "string", "enum": ["zoom", "reel"],
                   "description": "zoom = keep everything, add punch-ins at highlights; reel = also cut to only the highlights."},
         "intensity": {"type": "string", "enum": ["subtle", "normal", "hype"],
                       "description": "hype adds bigger zooms, flashes and a slow-motion beat on the best moment."},
         "target_seconds": {"type": "number", "description": "For reel: roughly how long the result should be. 30 is typical for TikTok."},
     }, ["style", "intensity", "target_seconds"])},
    {"name": "add_zoom", "description": "Add one smooth punch-in zoom between two output times (e.g. 'zoom in at 0:05').",
     "input_schema": _obj({"start": {"type": "number", "description": _SEC},
                           "end": {"type": "number", "description": _SEC},
                           "amount": {"type": "number", "description": "1.2 subtle to 1.6 strong."}},
                          ["start", "end", "amount"])},
    {"name": "remove_zooms", "description": "Remove every highlight / punch-in zoom.", "input_schema": _obj({})},
    {"name": "undo", "description": "Undo the most recent edit.", "input_schema": _obj({})},
]

READ_ONLY = {"get_transcript", "find_in_transcript"}

# One-click presets. They need no AI at all, so the app is useful even with no model set up.
RECIPES: dict[str, dict] = {
    "tiktok": {"label": "TikTok / Reels", "emoji": "📱", "description": "Vertical, follows your face, karaoke captions, no pauses",
               "steps": [("set_format", {"aspect": "9:16", "fit": "smart"}),
                         ("remove_silences", {"min_pause": 0.5, "threshold_db": -35}),
                         ("set_captions", {"enabled": True, "style": "karaoke", "position": "center"}),
                         ("set_audio", {"clean_voice": True, "voice_volume": 1, "normalize": True})]},
    "youtube": {"label": "YouTube", "emoji": "▶️", "description": "Widescreen, tighter pacing, clear audio, soft fades",
                "steps": [("set_format", {"aspect": "16:9", "fit": "blur"}),
                          ("remove_silences", {"min_pause": 0.8, "threshold_db": -35}),
                          ("set_audio", {"clean_voice": True, "voice_volume": 1, "normalize": True}),
                          ("set_transitions", {"between_clips": "none", "fade_in_out": True})]},
    "podcast": {"label": "Podcast clip", "emoji": "🎙️", "description": "Square, no ums, studio voice, clean captions",
                "steps": [("set_format", {"aspect": "1:1", "fit": "smart"}),
                          ("remove_filler_words", {"extra_words": []}),
                          ("remove_silences", {"min_pause": 0.7, "threshold_db": -35}),
                          ("set_audio", {"clean_voice": True, "voice_volume": 1, "normalize": True}),
                          ("set_captions", {"enabled": True, "style": "classic", "position": "bottom"})]},
    "gaming": {"label": "Gaming clip", "emoji": "🎮", "description": "Vertical with the whole screen kept, punchy colors, loud and clear",
               "steps": [("set_format", {"aspect": "9:16", "fit": "blur"}),
                         ("set_look", {"look": "vivid", "brightness": 0, "contrast": 0.2, "saturation": 0.2}),
                         ("set_audio", {"clean_voice": False, "voice_volume": 1, "normalize": True}),
                         ("auto_highlights", {"style": "zoom", "intensity": "normal", "target_seconds": 30}),
                         ("set_captions", {"enabled": True, "style": "bold", "position": "top"})]},
    "highlights": {"label": "Highlight reel", "emoji": "⚡", "description": "Only the best moments, hype zooms, flashes and slow-mo",
                   "steps": [("set_format", {"aspect": "9:16", "fit": "blur"}),
                             ("auto_highlights", {"style": "reel", "intensity": "hype", "target_seconds": 30}),
                             ("set_look", {"look": "vivid", "brightness": 0, "contrast": 0.2, "saturation": 0.2}),
                             ("set_audio", {"clean_voice": False, "voice_volume": 1, "normalize": True})]},
    "cinematic": {"label": "Cinematic", "emoji": "🎬", "description": "Film color grade with smooth fades",
                  "steps": [("set_look", {"look": "cinematic", "brightness": 0, "contrast": 0, "saturation": 0}),
                            ("set_transitions", {"between_clips": "fade", "fade_in_out": True})]},
    "slideshow": {"label": "Photo slideshow", "emoji": "🖼️", "description": "Gentle fades and warm colors for photos",
                  "steps": [("set_transitions", {"between_clips": "fade", "fade_in_out": True}),
                            ("set_look", {"look": "warm", "brightness": 0, "contrast": 0, "saturation": 0})]},
    "cleanup": {"label": "Quick clean-up", "emoji": "✨", "description": "Remove pauses and ums, fix the audio",
                "steps": [("remove_silences", {"min_pause": 0.6, "threshold_db": -35}),
                          ("remove_filler_words", {"extra_words": []}),
                          ("set_audio", {"clean_voice": True, "voice_volume": 1, "normalize": True})]},
}


# ---- argument coercion ----------------------------------------------------
# Small local models often send "2" for 2 or "0:15" for 15. Be forgiving.

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\s*$")


def _number(v: Any) -> float:
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().rstrip("sx%").strip()
    m = _TIME_RE.match(s)
    if m:
        h, mi, sec = m.groups()
        return int(h or 0) * 3600 + int(mi) * 60 + float(sec)
    return float(s)


def coerce(args: dict, schema: dict) -> dict:
    props, out = schema["properties"], {}
    for key, spec in props.items():
        if key not in args or args[key] is None:
            continue
        v, t = args[key], spec.get("type")
        try:
            if t == "integer":
                v = int(round(_number(v)))
            elif t == "number":
                v = _number(v)
            elif t == "boolean" and isinstance(v, str):
                v = v.strip().lower() in ("true", "yes", "on", "1")
            elif t == "array" and isinstance(v, str):
                v = [x.strip() for x in v.split(",") if x.strip()]
            elif t == "array" and spec["items"].get("type") == "object":
                v = [coerce(x, spec["items"]) for x in v if isinstance(x, dict)]
            elif t == "string":
                v = str(v)
        except (ValueError, TypeError):
            raise EditError(f"'{key}' should be a {t}, got {v!r}.") from None
        if "enum" in spec and v not in spec["enum"]:
            raise EditError(f"'{key}' must be one of {spec['enum']}, got {v!r}.")
        out[key] = v
    return out


# ---- the editor -----------------------------------------------------------


class Editor:
    def __init__(self, project: Project, transcriber: Transcriber,
                 on_status: Callable[[str], None] = lambda s: None):
        self.project = project
        self.history = History()
        self.transcriber = transcriber
        self.on_status = on_status
        self.version = 0  # bumps on every change; the UI uses it to refresh the preview
        self._schemas = {t["name"]: t["input_schema"] for t in TOOLS}
        self._analysis: dict[tuple, highlights.Analysis] = {}

    # Shared by the chat tools and the UI's buttons.
    def execute(self, name: str, raw_args: dict | None, record: bool = True) -> str:
        if name not in self._schemas:
            return f"Error: unknown tool '{name}'."
        snapshot = copy.deepcopy(self.project)
        try:
            args = coerce(raw_args or {}, self._schemas[name])
            if name == "undo":
                self.undo()
                return "Undid the last edit.\n" + self.project.summary()
            result = getattr(self, f"_t_{name}")(**args)
        except (EditError, TypeError) as e:
            self.project = snapshot  # never leave a half-applied edit behind
            return f"Error: {e}" if isinstance(e, EditError) else f"Error: bad arguments for {name}: {e}"
        if name not in READ_ONLY:
            if record:
                self._record(snapshot)
            self.version += 1
        return result

    def run_recipe(self, key: str) -> list[str]:
        """Apply a preset as a single undoable step. Steps that don't apply are skipped."""
        if key not in RECIPES:
            raise EditError(f"Unknown recipe '{key}'.")
        self._require_clips()
        snapshot = copy.deepcopy(self.project)
        results = [self.execute(name, args, record=False) for name, args in RECIPES[key]["steps"]]
        self._record(snapshot)
        self.version += 1
        return results

    def _record(self, snapshot: Project) -> None:
        self.history.undo_stack.append(snapshot)
        del self.history.undo_stack[:-self.history.limit]
        self.history.redo_stack.clear()

    def undo(self) -> None:
        self.project = self.history.undo(self.project)
        self.version += 1

    def redo(self) -> None:
        self.project = self.history.redo(self.project)
        self.version += 1

    # ---- helpers --------------------------------------------------------

    def words_for(self, media_id: str) -> list[Word]:
        m = self.project.media[media_id]
        if not m.has_audio:
            return []
        if not self.transcriber.is_cached(m.path):
            self.on_status(f"Transcribing {m.name}...")
        try:
            return self.transcriber.words(m.path)
        finally:
            self.on_status("")

    def words_by_media(self, only_if_cached: bool = False) -> dict[str, list[Word]]:
        out = {}
        for mid in {c.media_id for c in self.project.clips}:
            m = self.project.media[mid]
            if only_if_cached and m.has_audio and not self.transcriber.is_cached(m.path):
                continue
            out[mid] = self.words_for(mid)
        return out

    def ensure_focus(self) -> int:
        """Run face tracking on any video that smart framing needs. Returns how many had faces."""
        found = 0
        for mid in {c.media_id for c in self.project.clips}:
            m = self.project.media[mid]
            if m.kind != "video":
                continue
            if m.focus is None:
                self.on_status(f"Finding faces in {m.name}...")
                try:
                    m.focus = reframe.track_faces(m.path)
                finally:
                    self.on_status("")
            found += bool(m.focus)
        return found

    def analysis_for(self, media_id: str) -> highlights.Analysis:
        m = self.project.media[media_id]
        key = (m.path, m.duration)
        if key not in self._analysis:
            self.on_status(f"Finding the best moments in {m.name}...")
            try:
                self._analysis[key] = highlights.analyze(m.path, m.duration, m.has_audio)
            finally:
                self.on_status("")
        return self._analysis[key]

    def _src_to_out(self, media_id: str, t: float) -> float | None:
        for c, off in zip(self.project.clips, self.project.offsets()):
            if c.media_id == media_id and c.start <= t < c.end:
                return off + (t - c.start) / c.speed
        return None

    def _slow_mo(self, media_id: str, t: float) -> None:
        """Split out [t-0.25, t+0.45] of the clip containing t and play it at half speed."""
        for i, c in enumerate(self.project.clips):
            if c.media_id == media_id and c.start + 0.3 < t < c.end - 0.5 and c.speed >= 1:
                a, b = t - 0.25, t + 0.45
                pieces = [dataclasses.replace(c, end=a),
                          dataclasses.replace(c, id=self.project.new_id("c"), start=a, end=b, speed=c.speed * 0.5),
                          dataclasses.replace(c, id=self.project.new_id("c"), start=b)]
                self.project.clips[i:i + 1] = pieces
                return

    def _require_clips(self) -> None:
        if not self.project.clips:
            raise EditError("The timeline is empty. Import a video first.")

    def _clips(self, number: int) -> list[Clip]:
        self._require_clips()
        return list(self.project.clips) if number == 0 else [self.project.clip_at(number)]

    # ---- tools ----------------------------------------------------------

    def _t_trim_clip(self, clip: int, trim_start: float = 0.0, trim_end: float = 0.0) -> str:
        c = self.project.clip_at(clip)
        if trim_start < 0 or trim_end < 0:
            raise EditError("Trim amounts can't be negative.")
        # Amounts are in output seconds; convert for sped-up/slowed clips.
        trim_start, trim_end = trim_start * c.speed, trim_end * c.speed
        if trim_start + trim_end >= c.length - 0.1:
            raise EditError(f"Clip {clip} is only {c.out_length:.1f}s long; that would remove all of it. "
                            "Use delete_clip to remove a clip entirely.")
        c.start += trim_start
        c.end -= trim_end
        return f"Trimmed clip {clip}; it is now {c.out_length:.1f}s. Total length {fmt(self.project.duration)}."

    def _t_cut_section(self, start: float, end: float) -> str:
        self._require_clips()
        removed = cut_output_range(self.project, max(start, 0.0), end)
        return f"Cut {removed:.1f}s ({fmt(start)}-{fmt(end)}). New length {fmt(self.project.duration)}."

    def _t_keep_only(self, sections: list[dict]) -> str:
        self._require_clips()
        removed = keep_only_output(self.project, [(s["start"], s["end"]) for s in sections if "start" in s and "end" in s])
        return f"Kept {len(sections)} section(s), cut {removed:.1f}s. New length {fmt(self.project.duration)}."

    def _t_split_clip(self, clip: int, at: float) -> str:
        c = self.project.clip_at(clip)
        if not 0.05 < at < c.out_length - 0.05:
            raise EditError(f"Split point must be inside the clip (0-{c.out_length:.1f}s).")
        mid = c.start + at * c.speed
        right = Clip(self.project.new_id("c"), c.media_id, mid, c.end, c.speed, c.zoom)
        c.end = mid
        self.project.clips.insert(clip, right)
        return f"Split clip {clip} at {at:.1f}s into clips {clip} and {clip + 1}."

    def _t_delete_clip(self, clip: int) -> str:
        self.project.clip_at(clip)
        self.project.clips.pop(clip - 1)
        return f"Deleted clip {clip}. {len(self.project.clips)} clip(s) left, {fmt(self.project.duration)} total."

    def _t_move_clip(self, clip: int, to_position: int) -> str:
        c = self.project.clip_at(clip)
        n = len(self.project.clips)
        if not 1 <= to_position <= n:
            raise EditError(f"Position must be between 1 and {n}.")
        self.project.clips.pop(clip - 1)
        self.project.clips.insert(to_position - 1, c)
        return f"Moved clip {clip} to position {to_position}."

    def _t_add_clip(self, file: str) -> str:
        m = self.project.find_media(file)
        if m.kind == "audio":
            raise EditError(f"{m.name} is audio-only; use set_music for audio files.")
        self.project.clips.append(Clip(self.project.new_id("c"), m.id, 0.0, m.duration))
        if self.project.fit == "smart":
            self.ensure_focus()
        return f"Added {m.name} as clip {len(self.project.clips)}."

    def _t_set_speed(self, clip: int, speed: float) -> str:
        if not 0.25 <= speed <= 4:
            raise EditError("Speed must be between 0.25 (very slow) and 4 (very fast).")
        changed = [c for c in self._clips(clip) if self.project.media[c.media_id].kind != "image"]
        if not changed:
            raise EditError("Photos don't have a speed; use set_photo_length instead.")
        for c in changed:
            c.speed = speed
        which = "every clip" if clip == 0 else f"clip {clip}"
        return f"Set {which} to {speed:g}x speed. New length {fmt(self.project.duration)}."

    def _t_set_zoom(self, clip: int, zoom: float) -> str:
        zoom = min(max(zoom, 1.0), 2.0)
        for c in self._clips(clip):
            c.zoom = zoom
        return f"Zoom set to {zoom:g}x on {'every clip' if clip == 0 else f'clip {clip}'}."

    def _t_auto_highlights(self, style: str = "zoom", intensity: str = "normal", target_seconds: float = 30.0) -> str:
        self._require_clips()
        amount, flash_all, slow_mo, threshold = {"subtle": (1.2, False, False, 1.4), "normal": (1.35, False, False, 1.0),
                                                 "hype": (1.55, True, True, 0.8)}[intensity]
        videos = [mid for mid in dict.fromkeys(c.media_id for c in self.project.clips) if self.project.media[mid].kind == "video"]
        if not videos:
            raise EditError("Highlights need video clips; photos don't have action to find.")
        found: dict[str, list[highlights.Moment]] = {}
        for mid in videos:
            a = self.analysis_for(mid)
            ranges = [(c.start, c.end) for c in self.project.clips if c.media_id == mid]
            moments = highlights.find_moments(a, ranges, min_gap=3.5, threshold=threshold)
            if not moments:  # a calm video still has its best few seconds
                moments = highlights.find_moments(a, ranges, min_gap=3.5, threshold=-99, limit=3)
            found[mid] = moments
        everything = sorted((m.score, mid, m) for mid, ms in found.items() for m in ms)[::-1]
        if not everything:
            return "I couldn't find any standout moments to highlight."

        cut = 0.0
        already_short = self.project.duration <= target_seconds * 1.1
        if style == "reel" and not already_short:
            windows, total = [], 0.0
            for _, mid, m in everything:
                t = self._src_to_out(mid, m.time)
                if t is None:
                    continue
                win = (max(t - 3.0, 0.0), min(t + 2.0, self.project.duration))  # build-up + payoff
                windows.append(win)
                total += win[1] - win[0]
                if total >= target_seconds:
                    break
            if windows and sum(b - a for a, b in windows) < self.project.duration - 0.5:
                cut = keep_only_output(self.project, windows)

        # Fresh punch-ins (re-running replaces the old ones), attached in source time.
        best = everything[0][2]
        for c in self.project.clips:
            events = [[m.time - 0.35, m.time + 1.1, amount, m.cx, m.cy, 1.0 if (flash_all or m is best) and intensity != "subtle" else 0.0]
                      for m in found.get(c.media_id, []) if c.start - 0.5 < m.time < c.end + 0.5]
            c.zooms = events
        if slow_mo:
            self._slow_mo(everything[0][1], best.time)

        times = sorted(t for _, mid, m in everything if (t := self._src_to_out(mid, m.time)) is not None)
        msg = f"Added {len(times)} highlight zoom(s) at {', '.join(fmt(t) for t in times[:8])}"
        if cut:
            msg += f", and cut {cut:.0f}s of slower parts"
        elif style == "reel" and already_short:
            msg += f" (it's already under {target_seconds:.0f}s, so I kept every moment)"
        if slow_mo:
            msg += ", with a slow-motion beat on the best moment"
        return msg + f". New length {fmt(self.project.duration)}."

    def _t_add_zoom(self, start: float, end: float, amount: float = 1.35) -> str:
        self._require_clips()
        if end - start < 0.4:
            raise EditError("A zoom needs at least 0.4 seconds.")
        amount = min(max(amount, 1.05), 2.0)
        hit = 0
        for c, off in zip(self.project.clips, self.project.offsets()):
            a, b = max(start, off), min(end, off + c.out_length)
            if b - a < 0.2:
                continue
            s0, s1 = c.start + (a - off) * c.speed, c.start + (b - off) * c.speed
            c.zooms = [*c.zooms, [s0, s1, amount, 0.5, 0.5, 0.0]]
            hit += 1
        if not hit:
            raise EditError(f"That time isn't inside the video (0-{self.project.duration:.1f}s).")
        return f"Added a {amount:g}x zoom from {fmt(start)} to {fmt(end)}."

    def _t_remove_zooms(self) -> str:
        n = sum(len(c.zooms) for c in self.project.clips)
        for c in self.project.clips:
            c.zooms = []
        return f"Removed {n} zoom(s)." if n else "There were no zooms to remove."

    def _t_set_photo_length(self, clip: int, seconds: float) -> str:
        c = self.project.clip_at(clip)
        m = self.project.media[c.media_id]
        if m.kind != "image":
            raise EditError(f"Clip {clip} is a video; use trim_clip to shorten videos.")
        seconds = min(max(seconds, 0.5), 60.0)
        c.start, c.end = 0.0, seconds
        return f"Photo in clip {clip} now shows for {seconds:g}s."

    def _t_remove_silences(self, min_pause: float = 0.6, threshold_db: float = -35.0) -> str:
        self._require_clips()
        min_pause = min(max(min_pause, 0.2), 10.0)
        ranges = {}
        for mid in {c.media_id for c in self.project.clips}:
            m = self.project.media[mid]
            if not m.has_audio or m.kind != "video":
                continue
            self.on_status(f"Listening for pauses in {m.name}...")
            silent = media_mod.detect_silences(m.path, threshold_db, min_pause)
            ranges[mid] = media_mod.shrink(silent, padding=0.12)
        self.on_status("")
        n_before = len(self.project.clips)
        removed = remove_source_ranges(self.project, ranges)
        if removed < 0.05:
            return "Found no pauses long enough to cut. Try a shorter min_pause or a higher threshold_db (e.g. -30)."
        return (f"Removed {removed:.1f}s of silence ({len(self.project.clips) - n_before} new cuts). "
                f"New length {fmt(self.project.duration)}.")

    def _t_remove_filler_words(self, extra_words: list[str] | None = None) -> str:
        self._require_clips()
        ranges, count = {}, 0
        for mid in {c.media_id for c in self.project.clips}:
            hits = filler_ranges(self.words_for(mid), extra_words)
            # Pad slightly so the cut lands in the gap around the word, not mid-syllable.
            ranges[mid] = [(max(a - 0.04, 0), b + 0.04) for a, b in hits]
            count += len(hits)
        if not count:
            return "No filler words found."
        removed = remove_source_ranges(self.project, ranges)
        return f"Cut {count} filler word(s), {removed:.1f}s total. New length {fmt(self.project.duration)}."

    def _t_get_transcript(self) -> str:
        self._require_clips()
        placed = place_words(self.project, self.words_by_media())
        if not placed:
            return "No speech found in the edit."
        lines, cur, cur_start = [], [], 0.0
        for s, _, text in placed:
            if not cur:
                cur_start = s
            cur.append(text)
            if len(cur) >= 12 or text.endswith((".", "?", "!")):
                lines.append(f"[{fmt(cur_start)}] {' '.join(cur)}")
                cur = []
        if cur:
            lines.append(f"[{fmt(cur_start)}] {' '.join(cur)}")
        return "\n".join(lines)

    def _t_find_in_transcript(self, phrase: str) -> str:
        self._require_clips()
        if not phrase.strip():
            raise EditError("Give a word or phrase to search for.")
        placed = place_words(self.project, self.words_by_media())
        words = [Word(t, s, e) for s, e, t in placed]
        hits = search(words, phrase)
        if not hits:
            return f"'{phrase}' isn't said anywhere in the edit."
        return "\n".join(f"{fmt(h['start'])}-{fmt(h['end'])} (={h['start']:.2f}-{h['end']:.2f}s): ...{h['context']}..."
                         for h in hits[:10])

    def _t_set_captions(self, enabled: bool, style: str = "classic", position: str = "bottom") -> str:
        self._require_clips()
        cap = self.project.captions
        cap.enabled, cap.style, cap.position = enabled, style, position
        if not enabled:
            return "Captions turned off."
        n = sum(len(w) for w in self.words_by_media().values())  # transcribe now so preview is ready
        if not n:
            return "Captions are on, but no speech was detected in the clips."
        return f"Captions on ({style}, {position}) - {n} words transcribed."

    def _t_set_format(self, aspect: str, fit: str = "blur") -> str:
        self.project.aspect, self.project.fit = aspect, fit
        if fit == "smart" and self.project.clips:
            if not self.ensure_focus():
                return f"Format set to {aspect}. No faces were found, so it will center the frame instead."
            return f"Format set to {aspect}, following the speaker's face."
        return f"Format set to {aspect} ({fit})."

    def _t_set_look(self, look: str, brightness: float = 0.0, contrast: float = 0.0, saturation: float = 0.0) -> str:
        clamp = lambda v: round(min(max(v, -1.0), 1.0), 2)  # noqa: E731
        lk = self.project.look
        lk.name, lk.brightness, lk.contrast, lk.saturation = look, clamp(brightness), clamp(contrast), clamp(saturation)
        return f"Look set to {look}." if look != "none" else "Color look removed."

    def _t_set_audio(self, clean_voice: bool, voice_volume: float = 1.0, normalize: bool = True) -> str:
        a = self.project.audio
        a.clean_voice, a.voice_volume, a.normalize = clean_voice, min(max(voice_volume, 0.0), 2.0), normalize
        parts = ["voice cleanup " + ("on" if clean_voice else "off"),
                 f"voice at {round(a.voice_volume * 100)}%", "loudness levelling " + ("on" if normalize else "off")]
        return "Audio: " + ", ".join(parts) + "."

    def _t_set_music(self, file: str, volume: float = 0.25, duck: bool = True) -> str:
        if file.strip().lower() in ("none", "off", "remove", ""):
            self.project.music = None
            return "Music removed."
        m = self.project.find_media(file)
        if not m.has_audio:
            raise EditError(f"{m.name} has no audio track.")
        vol = min(max(volume, 0.0), 1.0)
        if vol < 0.02:  # adding a song at 0% is never what anyone means (small models send 0 for "default")
            vol = 0.25
        self.project.music = Music(m.id, vol, duck)
        return f"Music set to {m.name} at {round(vol * 100)}% volume{', ducking under speech' if duck else ''}."

    def _t_set_transitions(self, between_clips: str, fade_in_out: bool) -> str:
        self.project.transition, self.project.fade_ends = between_clips, fade_in_out
        return (f"Transitions between clips: {between_clips}. "
                f"Fade in/out at the ends: {'on' if fade_in_out else 'off'}.")

    def _t_add_text(self, text: str, start: float, end: float, position: str = "top") -> str:
        total = self.project.duration
        start, end = max(start, 0.0), min(end, total) if total else end
        if not text.strip():
            raise EditError("The text is empty.")
        if end - start < 0.2:
            raise EditError(f"Text needs a time range inside the video (0-{total:.1f}s).")
        self.project.texts.append(TextOverlay(text.strip(), start, end, position))
        return f"Added text {len(self.project.texts)}: \"{text.strip()}\" from {fmt(start)} to {fmt(end)}."

    def _t_remove_text(self, index: int) -> str:
        if not 1 <= index <= len(self.project.texts):
            raise EditError(f"There is no text {index}.")
        t = self.project.texts.pop(index - 1)
        return f"Removed text \"{t.text}\"."

    # ---- used by the import endpoint ------------------------------------

    def import_file(self, path: str) -> str:
        m = media_mod.probe(path, self.project.new_id("m"))
        self.history.push(self.project)
        self.project.media[m.id] = m
        if m.kind in ("video", "image"):
            self.project.clips.append(Clip(self.project.new_id("c"), m.id, 0.0, m.duration))
            if self.project.fit == "smart":
                self.ensure_focus()
        self.version += 1
        return m.id
