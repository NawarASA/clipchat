"""Speed, zoom, looks, audio, transitions, photos, highlights, recipes and smart reframing."""

import re
import subprocess

import pytest
from conftest import make_project
from test_tools import WORDS, FakeTranscriber

from clipchat import reframe
from clipchat.media import probe
from clipchat.render import PREVIEW, build_ass, build_command, build_srt, look_filter, render, sources_for
from clipchat.timeline import Clip, Look, Music, Project, cut_output_range, keep_only_output
from clipchat.tools import RECIPES, Editor
from test_render import _probe


# ---- timeline math with speed ------------------------------------------------

def test_speed_changes_duration_and_cut_mapping():
    p = make_project(10, 10)
    p.clips[0].speed = 2.0  # first clip now lasts 5s
    assert p.duration == pytest.approx(15)
    assert p.offsets() == [0, 5]
    cut_output_range(p, 1, 2)  # 1s of output = 2s of source in the fast clip
    assert [(c.start, c.end) for c in p.clips[:2]] == [(0, 2), (4, 10)]
    assert all(c.speed == 2.0 for c in p.clips[:2])


def test_keep_only_output():
    p = make_project(30)
    removed = keep_only_output(p, [(20, 25), (2, 5)])
    assert removed == pytest.approx(22)
    assert [(c.start, c.end) for c in p.clips] == [(2, 5), (20, 25)]


def test_project_roundtrip_with_new_fields():
    p = make_project(3)
    p.look = Look("warm", 0.2, 0, 0)
    p.transition, p.fade_ends = "fade", True
    p.clips[0].speed, p.clips[0].zoom = 1.5, 1.2
    assert Project.from_dict(p.to_dict()).to_dict() == p.to_dict()
    assert "1.5x speed" in p.summary() and "Look: warm" in p.summary()


# ---- tools -------------------------------------------------------------------

@pytest.fixture
def editor():
    return Editor(make_project(10, 10), FakeTranscriber(WORDS))


def test_speed_zoom_tools(editor):
    assert "2x speed" in editor.execute("set_speed", {"clip": 0, "speed": "2x"})
    assert editor.project.duration == pytest.approx(10)
    assert editor.execute("set_speed", {"clip": 1, "speed": 9}).startswith("Error")
    editor.execute("set_zoom", {"clip": 2, "zoom": 5})
    assert editor.project.clips[1].zoom == 2.0  # clamped


def test_trim_and_split_respect_speed(editor):
    editor.execute("set_speed", {"clip": 1, "speed": 2})
    editor.execute("trim_clip", {"clip": 1, "trim_start": 1, "trim_end": 0})  # 1 output second = 2 source
    assert editor.project.clips[0].start == pytest.approx(2)
    editor.execute("split_clip", {"clip": 1, "at": 1})
    assert editor.project.clips[0].out_length == pytest.approx(1)


def test_look_audio_transitions_music(editor):
    editor.execute("set_look", {"look": "cinematic", "brightness": 3, "contrast": 0, "saturation": -0.5})
    assert editor.project.look.brightness == 1.0
    editor.execute("set_audio", {"clean_voice": True, "voice_volume": 1.2, "normalize": True})
    assert editor.project.audio.clean_voice
    editor.execute("set_transitions", {"between_clips": "fade", "fade_in_out": True})
    assert editor.project.transition == "fade"
    assert editor.execute("set_look", {"look": "sepia", "brightness": 0, "contrast": 0, "saturation": 0}).startswith("Error")


def test_keep_only_tool_accepts_loose_numbers(editor):
    out = editor.execute("keep_only", {"sections": [{"start": "0:02", "end": "5"}, {"start": 12, "end": 14}]})
    assert "Kept 2" in out and editor.project.duration == pytest.approx(5)


def test_failed_edit_rolls_back(editor):
    before = editor.project.to_dict()
    assert editor.execute("keep_only", {"sections": []}).startswith("Error")
    assert editor.project.to_dict() == before


def test_recipe_is_one_undo_step(editor, monkeypatch):
    monkeypatch.setattr("clipchat.media.detect_silences", lambda *a, **k: [])
    monkeypatch.setattr(editor, "ensure_focus", lambda: 1)
    results = editor.run_recipe("tiktok")
    assert len(results) == len(RECIPES["tiktok"]["steps"])
    p = editor.project
    assert (p.aspect, p.fit, p.captions.style, p.audio.clean_voice) == ("9:16", "smart", "karaoke", True)
    editor.undo()
    assert editor.project.aspect == "original" and not editor.project.captions.enabled


def test_photo_tools(editor):
    editor.project.media["p1"] = __import__("clipchat.timeline", fromlist=["Media"]).Media(
        "p1", "/x.jpg", "x.jpg", "image", 4.0, 800, 600, has_audio=False, focus=[])
    editor.execute("add_clip", {"file": "x.jpg"})
    assert "6s" in editor.execute("set_photo_length", {"clip": 3, "seconds": 6})
    assert editor.execute("set_speed", {"clip": 3, "speed": 2}).startswith("Error")
    assert editor.execute("set_photo_length", {"clip": 1, "seconds": 6}).startswith("Error")


# ---- reframe ------------------------------------------------------------------

def _eval_ffmpeg_expr(expr: str, t: float) -> float:
    py = expr.replace("if(", "_if(").replace("lt(", "_lt(")
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "t": t})


def test_smooth_track_fills_gaps_and_rejects_noise():
    raw = [(0.3, 0.5), None, (0.32, 0.5), (0.9, 0.1), (0.31, 0.5), (0.3, 0.5), None]
    track = reframe.smooth_track([i * 0.5 for i in range(7)], raw)
    assert len(track) == 7
    assert all(0.28 < x < 0.4 for _, x, _ in track)  # the one-frame jump to 0.9 is filtered out
    assert reframe.smooth_track([0, 1, 2], [None, None, None]) == []


def test_keyframes_and_lerp_expression():
    track = [[t, 0.2 + 0.1 * t, 0.5] for t in range(0, 11)]
    keys = reframe.keyframes(track, 2.0, 6.0, axis=1)
    expr = reframe.lerp_expr(keys)
    assert _eval_ffmpeg_expr(expr, 0.0) == pytest.approx(0.4)  # clip-relative: source 2s
    assert _eval_ffmpeg_expr(expr, 1.5) == pytest.approx(0.55)
    assert _eval_ffmpeg_expr(expr, 99) == pytest.approx(keys[-1][1])


def test_no_faces_in_test_pattern(sample_video):
    assert reframe.track_faces(str(sample_video)) == []


# ---- rendering -----------------------------------------------------------------

def test_look_filter_and_fades_in_graph():
    p = make_project(3, 3)
    p.look = Look("black & white", 0.5, 0, 0)
    p.transition, p.fade_ends = "fade", True
    assert "hue=s=0" in look_filter(p.look) and "brightness=0.075" in look_filter(p.look)
    srcs = sources_for(p)
    assert (srcs[0].fade_in, srcs[0].fade_out, srcs[1].fade_in, srcs[1].fade_out) == (0.6, 0.25, 0.25, 0.8)


def test_smart_crop_follows_track(tmp_path):
    p = make_project(4)
    p.media["m0"].focus = [[t, 0.25, 0.5] for t in range(5)]
    p.aspect, p.fit = "9:16", "smart"
    _, graph = build_command(sources_for(p), 360, 640, "smart", PREVIEW, tmp_path / "x.mp4")
    m = re.search(r"crop=(\d+):(\d+):x='([^']+)'", graph)
    assert m and (int(m.group(1)), int(m.group(2))) == (606, 1080)  # 9:16 window out of 1920x1080
    x = _eval_ffmpeg_expr(m.group(3).replace("max(", "max(").replace("min(", "min("), 1.0)
    assert x == pytest.approx(0.25 * 1920 - 303)  # face at 25% from the left


def test_karaoke_and_srt():
    p = make_project(10)
    p.captions.enabled, p.captions.style = True, "karaoke"
    words = {"m0": WORDS}
    ass = build_ass(p, words, 1080, 1920)
    assert "{\\k" in ass and "&H0000E1FF" in ass
    srt = build_srt(p, words)
    assert srt.startswith("1\n00:00:00,000 --> ") and "pricing." in srt


def test_full_render_with_every_effect(tmp_path, sample_video, sample_music):
    p = Project()
    p.media["m1"] = probe(str(sample_video), "m1")
    m = probe(str(sample_music), "m2")
    p.media["m2"] = m
    p.clips = [Clip("c1", "m1", 0, 2, speed=2.0, zoom=1.3), Clip("c2", "m1", 3.5, 6.0, speed=0.5)]
    p.aspect, p.fit = "1:1", "smart"
    p.media["m1"].focus = [[t / 2, 0.4, 0.5] for t in range(13)]
    p.look = Look("vintage", 0.2, 0.1, -0.2)
    p.audio.clean_voice, p.audio.voice_volume = True, 1.2
    p.music = Music("m2", 0.4, duck=True)
    p.transition, p.fade_ends = "fade", True
    p.captions.enabled, p.captions.style = True, "karaoke"
    out = render(p, tmp_path / "all.mp4", PREVIEW, {"m1": [WORDS[0]]})
    dur, w, h, _ = _probe(out)
    assert (w, h) == (640, 640)
    assert dur == pytest.approx(1 + 5, abs=0.2)  # 2s at 2x + 2.5s at 0.5x


def test_photo_slideshow_render(tmp_path, sample_video):
    img = tmp_path / "photo.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=800x600",
                    "-frames:v", "1", str(img)], check=True)
    p = Project()
    p.media["p"] = probe(str(img), "p")
    assert p.media["p"].kind == "image" and not p.media["p"].has_audio
    p.media["v"] = probe(str(sample_video), "v")
    p.clips = [Clip("c1", "p", 0, 2.5), Clip("c2", "v", 0, 1.5)]
    p.aspect, p.transition = "9:16", "fade"
    out = render(p, tmp_path / "show.mp4", PREVIEW, {})
    assert _probe(out)[0] == pytest.approx(4.0, abs=0.2)
