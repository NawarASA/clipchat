import subprocess
from pathlib import Path

import pytest

from clipchat.timeline import Clip, Media, Project


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """6s 320x240 clip: tone 0-2s, silence 2-3.5s, tone 3.5-6s."""
    out = tmp_path_factory.mktemp("media") / "talk.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6",
        "-f", "lavfi", "-i", "aevalsrc='if(between(t,2,3.5),0,0.5*sin(440*2*PI*t))':s=48000:d=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out


@pytest.fixture(scope="session")
def sample_music(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("media") / "song.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=3", str(out)], check=True)
    return out


def make_project(*lengths: float) -> Project:
    """A project with one 60s source and clips of the given lengths laid end to end in source time."""
    p = Project()
    p.media["m0"] = Media("m0", "/fake/a.mp4", "a.mp4", "video", 60.0, 1920, 1080)
    t = 0.0
    for n in lengths:
        p.clips.append(Clip(p.new_id("c"), "m0", t, t + n))
        t += n
    return p
