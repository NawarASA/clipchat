import pytest
from conftest import make_project

from clipchat.tools import TOOLS, Editor, coerce
from clipchat.transcribe import Word, filler_ranges, search


class FakeTranscriber:
    def __init__(self, words):
        self._words = words

    def is_cached(self, path):
        return True

    def words(self, path):
        return self._words


WORDS = [Word("So", 0.0, 0.3), Word("um,", 0.5, 0.8), Word("today", 1.0, 1.4), Word("we", 1.5, 1.6),
         Word("talk", 1.7, 2.0), Word("about", 2.1, 2.4), Word("pricing.", 2.5, 3.0),
         Word("You", 3.2, 3.4), Word("know", 3.4, 3.6), Word("it's", 3.8, 4.0), Word("cheap.", 4.1, 4.6)]


@pytest.fixture
def editor():
    return Editor(make_project(10), FakeTranscriber(WORDS))


def test_every_tool_has_an_implementation_and_strict_schema(editor):
    for t in TOOLS:
        assert t["name"] == "undo" or hasattr(editor, f"_t_{t['name']}"), t["name"]
        schema = t["input_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])


def test_coerce_forgives_local_model_quirks():
    schema = next(t for t in TOOLS if t["name"] == "cut_section")["input_schema"]
    assert coerce({"start": "0:10", "end": "15s"}, schema) == {"start": 10.0, "end": 15.0}
    trim = next(t for t in TOOLS if t["name"] == "trim_clip")["input_schema"]
    assert coerce({"clip": "2", "trim_start": 1, "bogus": 3}, trim) == {"clip": 2, "trim_start": 1.0}


def test_coerce_rejects_bad_enum(editor):
    out = editor.execute("set_format", {"aspect": "4:3", "fit": "crop"})
    assert out.startswith("Error") and "9:16" in out


def test_trim_then_undo(editor):
    v0 = editor.version
    assert "now 7.0s" in editor.execute("trim_clip", {"clip": 1, "trim_start": 2, "trim_end": 1})
    assert editor.version == v0 + 1
    assert "Undid" in editor.execute("undo", {})
    assert editor.project.clips[0].length == 10


def test_errors_do_not_change_state_or_history(editor):
    out = editor.execute("trim_clip", {"clip": 5, "trim_start": 1, "trim_end": 0})
    assert out.startswith("Error") and "no clip 5" in out
    assert not editor.history.undo_stack


def test_split_move_delete(editor):
    editor.execute("split_clip", {"clip": 1, "at": 4})
    assert [(c.start, c.end) for c in editor.project.clips] == [(0, 4), (4, 10)]
    editor.execute("move_clip", {"clip": 2, "to_position": 1})
    assert editor.project.clips[0].start == 4
    editor.execute("delete_clip", {"clip": 2})
    assert len(editor.project.clips) == 1


def test_filler_removal_cuts_um_and_you_know(editor):
    out = editor.execute("remove_filler_words", {"extra_words": []})
    assert "Cut 2 filler" in out
    kept = editor.project.clips
    assert all(not (c.start < 0.65 < c.end) for c in kept)  # "um" gone
    assert all(not (c.start < 3.5 < c.end) for c in kept)  # "you know" gone


def test_transcript_uses_output_time_after_cut(editor):
    editor.execute("cut_section", {"start": 0, "end": 1})
    hits = editor.execute("find_in_transcript", {"phrase": "talk about"})
    assert "0:00.7" in hits  # spoken at 1.7s in source, 1s was cut before it
    assert "pricing" in editor.execute("get_transcript", {})


def test_read_only_tools_dont_bump_version(editor):
    v = editor.version
    editor.execute("get_transcript", {})
    assert editor.version == v


def test_captions_music_text(editor):
    assert "11 words" in editor.execute("set_captions", {"enabled": True, "style": "bold", "position": "center"})
    assert "isn't imported" not in editor.execute("set_music", {"file": "none", "volume": 0.3})
    from clipchat.timeline import Media
    editor.project.media["s"] = Media("s", "/x/song.mp3", "song.mp3", "audio", 30.0)
    assert "25% volume" in editor.execute("set_music", {"file": "song.mp3", "volume": 0, "duck": True})
    out = editor.execute("set_music", {"file": "missing.mp3", "volume": 0.3})
    assert out.startswith("Error") and "a.mp4" in out
    assert "Added text 1" in editor.execute("add_text", {"text": "Hi", "start": 0, "end": 99, "position": "top"})
    assert editor.project.texts[0].end == 10  # clamped to video length


def test_filler_ranges_and_search_helpers():
    assert filler_ranges(WORDS) == [(0.5, 0.8), (3.2, 3.6)]
    assert filler_ranges(WORDS, ["cheap"])[-1] == (4.1, 4.6)
    assert search(WORDS, "Pricing") and not search(WORDS, "")
