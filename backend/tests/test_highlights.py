"""Auto highlights: finding action, punch-in zooms, highlight reels, slow-mo beats."""

import subprocess

import pytest
from test_render import _probe

from clipchat import highlights
from clipchat.media import probe
from clipchat.render import PREVIEW, punch_in_filter, render, sources_for
from clipchat.timeline import Clip, Project, cut_output_range
from clipchat.tools import Editor

BURSTS = [(3.0, 4.0), (8.0, 9.0)]  # loud + busy moments; everything else is calm


class NoTranscriber:
    def is_cached(self, p):
        return True

    def words(self, p):
        return []


@pytest.fixture(scope="module")
def action_video(tmp_path_factory):
    """12s clip: a still scene with quiet hiss, except two bursts where a box whizzes around and it gets loud."""
    out = tmp_path_factory.mktemp("hl") / "game.mp4"
    busy = "+".join(f"between(t,{a},{b})" for a, b in BURSTS)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x202040:s=320x240:d=12:r=30",
        "-f", "lavfi", "-i", "color=c=white:s=60x60:d=12:r=30",
        "-f", "lavfi", "-i", f"aevalsrc='0.01*random(0)+if({busy},0.8*sin(2*PI*600*t),0)':s=48000:d=12",
        "-filter_complex", f"[0][1]overlay=x='if({busy},mod(t*700\\,260),20)':y=90:shortest=1[v]",
        "-map", "[v]", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out),
    ], check=True)
    return out


def _editor(path):
    p = Project()
    m = probe(str(path), "m1")
    p.media["m1"] = m
    p.clips = [Clip("c1", "m1", 0, m.duration)]
    return Editor(p, NoTranscriber())


def test_analysis_finds_the_action(action_video):
    a = highlights.analyze(str(action_video), 12.0)
    moments = highlights.find_moments(a, [(0, 12)])
    times = [m.time for m in moments]
    assert len(times) == 2
    for (s, e), t in zip(BURSTS, times):
        assert s - 0.3 <= t <= e + 0.3, times


def test_zoom_style_adds_punch_ins_without_cutting(action_video):
    ed = _editor(action_video)
    out = ed.execute("auto_highlights", {"style": "zoom", "intensity": "normal", "target_seconds": 30})
    assert out.startswith("Added 2 highlight zoom")
    zooms = ed.project.clips[0].zooms
    assert len(zooms) == 2 and all(z[2] == pytest.approx(1.35) for z in zooms)
    assert ed.project.duration == pytest.approx(12, abs=0.1)
    ed.undo()
    assert ed.project.clips[0].zooms == []


def test_reel_cuts_to_the_highlights(action_video):
    ed = _editor(action_video)
    out = ed.execute("auto_highlights", {"style": "reel", "intensity": "normal", "target_seconds": 6})
    assert "cut" in out
    assert ed.project.duration == pytest.approx(10, abs=1.0)  # two 5s windows
    kept = [(c.start, c.end) for c in ed.project.clips]
    for s, e in BURSTS:
        assert any(a <= s + 0.5 and b >= s + 0.5 for a, b in kept), kept


def test_hype_adds_slow_mo_and_flash_and_renders(tmp_path, action_video):
    ed = _editor(action_video)
    ed.execute("auto_highlights", {"style": "zoom", "intensity": "hype", "target_seconds": 30})
    assert any(c.speed == 0.5 for c in ed.project.clips)  # the slow-motion beat
    assert ed.project.duration == pytest.approx(12 + 0.7, abs=0.05)  # 0.7s window at half speed
    graph_bits = [punch_in_filter(s, 360, 640, "blur") for s in sources_for(ed.project) if s.zooms]
    assert graph_bits and all("zoompan=" in g for g in graph_bits) and any("eq=brightness" in g for g in graph_bits)
    ed.project.aspect = "9:16"
    out = render(ed.project, tmp_path / "hype.mp4", PREVIEW, {})
    assert _probe(out)[0] == pytest.approx(ed.project.duration, abs=0.2)


def test_reel_keeps_short_videos_whole(action_video):
    ed = _editor(action_video)
    out = ed.execute("auto_highlights", {"style": "reel", "intensity": "normal", "target_seconds": 30})
    assert "kept every moment" in out
    assert ed.project.duration == pytest.approx(12, abs=0.1)
    assert len(ed.project.clips[0].zooms) == 2


def test_manual_zoom_and_zooms_survive_cuts(action_video):
    ed = _editor(action_video)
    assert "1.4x zoom" in ed.execute("add_zoom", {"start": 6, "end": 7, "amount": 1.4})
    cut_output_range(ed.project, 1, 2)  # cutting earlier footage shifts output time...
    c = next(c for c in ed.project.clips if c.start <= 6.5 < c.end)
    assert c.zooms[-1][:2] == [6, 7]  # ...but the zoom stays on the same moment (source time)
    assert "Removed" in ed.execute("remove_zooms", {})
    assert all(not c.zooms for c in ed.project.clips)
    assert ed.execute("add_zoom", {"start": 50, "end": 51, "amount": 1.4}).startswith("Error")
