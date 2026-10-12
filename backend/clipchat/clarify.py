"""Catch the common ambiguous requests before the model guesses.

Small local models almost never decide to ask; they just pick something. These checks are cheap,
deterministic and only fire when the answer would change the result a lot, so they never get in
the way of a clear request. The user's answer is then sent to the model together with the
original request.
"""

from __future__ import annotations

import re

from .timeline import Project

# Words that already say what "better" means, so a vague request isn't vague after all.
_SPECIFIC = re.compile(r"\b(caption|subtitle|music|song|vertical|tik ?tok|reels?|shorts?|square|pause|silen|ums?|uhs?|"
                       r"filler|noise|audio|sound|cinematic|warm|cool|vintage|bright|dark|colou?r|zoom|highlight|"
                       r"fade|transition|speed|slow|fast|title|text|crop|cut)", re.I)


# What each canned option means, spelled out for the model so "Richer colors" can't become "cinematic".
MEANINGS = {
    "Richer colors": "set_look vivid", "Cinematic look": "set_look cinematic",
    "Clearer voice": "set_audio with clean_voice on", "Remove pauses": "remove_silences",
    "Add captions": "set_captions on",
    "About 15 seconds": "auto_highlights style reel target 15 for action footage, otherwise keep_only the best 15s",
    "About 30 seconds": "auto_highlights style reel target 30 for action footage, otherwise keep_only the best 30s",
    "About 60 seconds": "auto_highlights style reel target 60 for action footage, otherwise keep_only the best 60s",
    "Just remove the slow parts": "remove_silences",
}


def _ask(question: str, options: list[str], multi: bool) -> dict:
    return {"question": question, "options": options, "multi": multi}


def clarify(text: str, project: Project) -> dict | None:
    low = text.lower()
    if not project.clips:
        return None

    # "Add music" when several songs are imported and none is named.
    audio = [m.name for m in project.media.values() if m.kind == "audio"]
    wants_music = re.search(r"\b(music|song|soundtrack|beat|track)\b", low)
    removing = re.search(r"\b(remove|no|without|stop|turn off|mute|less|quieter|lower)\b", low)
    if wants_music and not removing and len(audio) > 1:
        generic = {"music", "song", "track", "audio", "sound", "beat", "soundtrack", "background"}
        named = [n for n in audio if (stem := n.lower().rsplit(".", 1)[0]) not in generic and stem in low]
        if not named:
            return _ask("Which song should I use?", audio[:5], False)

    # "Make it shorter" with no target, on a video long enough for that to matter.
    if (re.search(r"\b(shorter|shorten|cut it down|trim it down|make it short|condense|too long)\b", low)
            and not re.search(r"\d", low) and project.duration > 45):
        return _ask("How long should it be?",
                    ["About 15 seconds", "About 30 seconds", "About 60 seconds", "Just remove the slow parts"], False)

    # Vague "make it better" with nothing specific: let them tick what they mean.
    if (re.search(r"\b(nicer|better|improve|enhance|polish|fix it|look good|professional|pro|cooler|amazing)\b", low)
            and not _SPECIFIC.search(low)):
        return _ask("What would you like me to improve?",
                    ["Richer colors", "Cinematic look", "Clearer voice", "Remove pauses", "Add captions"], True)
    return None


def combine(original: str, question: str, answer: str) -> str:
    """The message the model sees once the user has answered."""
    meant = [MEANINGS[o.strip()] for o in answer.split(",") if o.strip() in MEANINGS]
    how = f" That means: {'; '.join(meant)}." if meant else ""
    return f"{original}\n\n(I asked \"{question}\" and the user chose: {answer}.{how} Do exactly that, nothing else.)"
