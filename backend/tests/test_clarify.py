from conftest import make_project

from clipchat.clarify import clarify, combine
from clipchat.timeline import Media


def _with_songs(*names, length=60):
    p = make_project(length)
    for i, n in enumerate(names):
        p.media[f"a{i}"] = Media(f"a{i}", f"/x/{n}", n, "audio", 30.0)
    return p


def test_asks_which_song_only_when_it_matters():
    p = _with_songs("beat.mp3", "chill.mp3")
    q = clarify("add some background music", p)
    assert q["options"] == ["beat.mp3", "chill.mp3"] and not q["multi"]
    assert clarify("add the chill song", p) is None  # named it
    generic = _with_songs("music.mp3", "chill.mp3")
    assert clarify("add some background music", generic) is not None  # "music" isn't naming music.mp3
    assert clarify("remove the music", p) is None
    assert clarify("add some background music", _with_songs("beat.mp3")) is None  # only one choice


def test_asks_how_short_only_for_long_videos_without_a_number():
    assert clarify("make it shorter", _with_songs(length=120))["question"] == "How long should it be?"
    assert clarify("make it shorter, about 30 seconds", _with_songs(length=120)) is None
    assert clarify("make it shorter", _with_songs(length=20)) is None


def test_vague_requests_get_tickboxes_specific_ones_dont():
    p = _with_songs()
    q = clarify("make it look nicer", p)
    assert q["multi"] and "Richer colors" in q["options"]
    assert clarify("make it nicer with captions", p) is None
    assert clarify("remove the pauses", p) is None


def test_combine_carries_the_answer():
    msg = combine("make it look nicer", "What would you like me to improve?", "Richer colors, Add captions")
    assert msg.startswith("make it look nicer") and "Richer colors, Add captions" in msg
    assert "set_look vivid" in msg and "set_captions on" in msg
