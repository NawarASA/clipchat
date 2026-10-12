import json
import subprocess

import pytest
from conftest import make_project

from clipchat import render as render_mod
from clipchat.media import detect_silences, probe, shrink
from clipchat.render import PREVIEW, Quality, build_ass, caption_lines, output_size, render
from clipchat.timeline import Clip, Music, Project
from clipchat.tools import Editor
from clipchat.transcribe import Word


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, check=True)
    info = json.loads(out.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return float(info["format"]["duration"]), v["width"], v["height"], info["streams"]


def test_output_size_presets():
    p = make_project(5)
    assert output_size(p, Quality(None, "x", 1)) == (1920, 1080)
    p.aspect = "9:16"
    assert output_size(p, PREVIEW) == (360, 640)


def test_caption_lines_follow_edit():
    p = make_project(2, 2)
    p.clips.reverse()  # source 2-4 plays first
    words = {"m0": [Word("a", 0.1, 0.4), Word("b", 2.1, 2.4), Word("c", 2.5, 2.8)]}
    lines = caption_lines(p, words, max_words=5)
    assert lines[0][2] == "b c" and lines[0][0] == pytest.approx(0.1)
    assert lines[1][2] == "a" and lines[1][0] == pytest.approx(2.1)


def test_build_ass_escapes_and_positions():
    p = make_project(5)
    p.captions.enabled, p.captions.style, p.captions.position = True, "bold", "top"
    ass = build_ass(p, {"m0": [Word("{hi}", 0, 1)]}, 1080, 1920)
    assert "(HI)" in ass and "{hi}" not in ass
    assert ",8,40,40," in ass  # top alignment


def test_detect_silences(sample_video):
    ranges = detect_silences(str(sample_video), -35, 0.5)
    assert len(ranges) == 1
    a, b = ranges[0]
    assert a == pytest.approx(2.0, abs=0.1) and b == pytest.approx(3.5, abs=0.1)
    assert shrink(ranges, 0.1)[0][0] == pytest.approx(a + 0.1)


def test_render_vertical_with_cuts_text_and_music(tmp_path, sample_video, sample_music):
    p = Project()
    v = probe(str(sample_video), "m1")
    m = probe(str(sample_music), "m2")
    m.kind = "audio"
    p.media = {"m1": v, "m2": m}
    p.clips = [Clip("c1", "m1", 3.5, 6.0), Clip("c2", "m1", 0.0, 2.0)]  # reordered
    p.aspect = "9:16"
    p.music = Music("m2", 0.3)
    p.captions.enabled = True
    p.texts = []
    from clipchat.timeline import TextOverlay
    p.texts.append(TextOverlay("Hello", 0, 2))
    progress = []
    out = render(p, tmp_path / "out.mp4", PREVIEW, {"m1": [Word("hey", 4.0, 4.5)]}, progress.append)
    dur, w, h, streams = _probe(out)
    assert (w, h) == (360, 640)
    assert dur == pytest.approx(4.5, abs=0.15)
    assert any(s["codec_type"] == "audio" for s in streams)
    assert progress[-1] == 1.0


def test_remove_silences_end_to_end(tmp_path, sample_video):
    class NoTranscriber:
        def is_cached(self, p):
            return True

        def words(self, p):
            return []

    p = Project()
    p.media["m1"] = probe(str(sample_video), "m1")
    p.clips = [Clip("c1", "m1", 0, p.media["m1"].duration)]
    ed = Editor(p, NoTranscriber())
    assert "Removed" in ed.execute("remove_silences", {"min_pause": 0.5, "threshold_db": -35})
    assert len(ed.project.clips) == 2
    assert ed.project.duration == pytest.approx(6 - 1.5 + 0.24, abs=0.15)
    out = render(ed.project, tmp_path / "cut.mp4", PREVIEW, {})
    assert _probe(out)[0] == pytest.approx(ed.project.duration, abs=0.15)


def test_long_timelines_render_in_chunks(tmp_path, sample_video, monkeypatch):
    monkeypatch.setattr(render_mod, "CHUNK", 3)
    p = Project()
    p.media["m1"] = probe(str(sample_video), "m1")
    p.clips = [Clip(f"c{i}", "m1", i * 0.5, i * 0.5 + 0.4) for i in range(7)]
    out = render(p, tmp_path / "chunked.mp4", PREVIEW, {})
    assert _probe(out)[0] == pytest.approx(2.8, abs=0.2)


def test_thumbnails_for_video_and_photo(tmp_path, sample_video):
    from clipchat.media import thumbnail
    img = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=400x300",
                    "-frames:v", "1", str(img)], check=True)
    for src in (sample_video, img):
        out = thumbnail(probe(str(src), "x"), tmp_path / f"{src.stem}.jpg")
        assert out.exists() and out.stat().st_size > 0


def test_probe_reads_rotation_and_cover_art(tmp_path, sample_video):
    rotated = tmp_path / "rot.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-display_rotation", "90", "-i", str(sample_video),
                    "-c", "copy", str(rotated)], check=True)
    m = probe(str(rotated), "r")
    assert (m.width, m.height, m.kind) == (240, 320, "video")
    song = tmp_path / "song.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-f", "lavfi",
                    "-i", "color=red:s=64x64:d=1", "-map", "0", "-map", "1", "-frames:v", "1", "-c:v", "mjpeg",
                    "-disposition:v", "attached_pic", str(song)], check=True)
    assert probe(str(song), "s").kind == "audio"



def test_ffmpeg_never_inherits_stdin(tmp_path, monkeypatch):
    """Regression: the packaged engine blocks reading its stdin to notice the app closing. FFmpeg
    inherited that pipe and froze at 0% (only reproducible in the frozen build), so every FFmpeg
    launch must use -nostdin and stdin=DEVNULL."""
    args, _ = render_mod.build_command([render_mod.Source("x.mp4", 0, 1, True)], 64, 64, "crop", PREVIEW, tmp_path / "o.mp4")
    assert "-nostdin" in args
    seen = []

    class FakePopen:
        def __init__(self, a, **kw):
            seen.append(kw.get("stdin"))
            self.stdout, self.stderr, self.returncode = iter([]), iter([]), 0

        def wait(self):
            return 0

    monkeypatch.setattr(render_mod.subprocess, "Popen", FakePopen)
    render_mod._run(args, "", tmp_path, 1.0, lambda p: None)
    assert seen == [subprocess.DEVNULL]
