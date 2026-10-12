"""Turn a Project into an MP4 with one FFmpeg filter graph.

Each clip is opened as its own seeked input (``-ss/-t``), so clips can be in any
order without FFmpeg buffering decoded frames. Every clip gets its own chain
(smart crop / fit, zoom, speed, color look, fades); then the joined video gets
captions and titles (an ASS subtitle file burned in by libass) and the joined
audio gets voice cleanup, music with automatic ducking, and loudness levelling.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .media import _NO_WINDOW, ffmpeg_bin
from .reframe import keyframes, lerp_expr
from .timeline import Look, Project
from .transcribe import Word

CHUNK = 40  # clips per FFmpeg pass; keeps the command line under Windows' 32K limit
FPS = 30


@dataclass
class Quality:
    max_side: int | None  # None = full resolution
    preset: str
    crf: int


PREVIEW = Quality(max_side=640, preset="ultrafast", crf=30)
EXPORT = Quality(max_side=None, preset="medium", crf=20)
INTERMEDIATE_CRF = 14


def export_quality(resolution: str = "full", quality: str = "high") -> Quality:
    side = {"full": None, "1080p": 1920, "720p": 1280}.get(resolution)
    crf = {"high": 18, "standard": 21, "small": 26}.get(quality, 20)
    return Quality(side, "medium", crf)


@dataclass
class Source:
    path: str
    start: float
    length: float  # source seconds to read
    has_audio: bool
    kind: str = "video"  # "video" | "image" | "rendered" (an intermediate chunk)
    speed: float = 1.0
    zoom: float = 1.0
    src_w: int = 0
    src_h: int = 0
    focus: list | None = None
    fade_in: float = 0.0
    fade_out: float = 0.0
    zooms: list | None = None

    @property
    def out_length(self) -> float:
        return self.length / self.speed


@dataclass
class Final:
    """Whole-video processing, applied once in the last pass."""
    total: float
    subtitles: str | None = None
    music: tuple[str, float, bool] | None = None  # path, volume, duck
    clean_voice: bool = False
    voice_volume: float = 1.0
    normalize: bool = True


# ---- geometry ------------------------------------------------------------


def output_size(project: Project, quality: Quality) -> tuple[int, int]:
    if project.aspect == "9:16":
        w, h = 1080, 1920
    elif project.aspect == "1:1":
        w, h = 1080, 1080
    elif project.aspect == "16:9":
        w, h = 1920, 1080
    else:
        first = next((project.media[c.media_id] for c in project.clips), None)
        w, h = (first.width, first.height) if first and first.width else (1920, 1080)
    if quality.max_side and max(w, h) > quality.max_side:
        scale = quality.max_side / max(w, h)
        w, h = w * scale, h * scale
    return _even(w), _even(h)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _floor_even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


def fit_filter(fit: str, w: int, h: int, tag: str) -> str:
    """Filter chain that maps an arbitrary frame onto a w*h canvas."""
    if fit in ("crop", "smart"):  # "smart" without a face track falls back to a centered crop
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "pad":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    # "blur": the CapCut look - the frame sits on a blurred, zoomed copy of itself.
    sw, sh = _even(w / 8), _even(h / 8)
    return (f"split=2[bg{tag}][fg{tag}];"
            f"[bg{tag}]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
            f"boxblur=6:2,scale={w}:{h},setsar=1[bb{tag}];"
            f"[fg{tag}]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[ff{tag}];"
            f"[bb{tag}][ff{tag}]overlay=(W-w)/2:(H-h)/2")


def smart_filter(src: Source, w: int, h: int) -> str | None:
    """An animated crop that follows the tracked face, or None if there's nothing to follow."""
    if not src.focus or not src.src_w:
        return None
    sw, sh = src.src_w, src.src_h
    cw = min(_floor_even(sh * w / h), _floor_even(sw))
    ch = min(_floor_even(sw * h / w), _floor_even(sh))
    end = src.start + src.length
    kx, ky = keyframes(src.focus, src.start, end, 1), keyframes(src.focus, src.start, end, 2)
    if not kx:
        return None
    xe, ye = lerp_expr(kx), lerp_expr(ky)
    x = f"max(0,min({sw - cw},({xe})*{sw}-{cw / 2:.1f}))"
    y = f"max(0,min({sh - ch},({ye})*{sh}-{ch / 2:.1f}))"
    return f"crop={cw}:{ch}:x='{x}':y='{y}',scale={w}:{h},setsar=1"


_LOOKS = {
    "warm": "colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.05:bm=-0.05,eq=saturation=1.08",
    "cool": "colorbalance=rs=-0.06:bs=0.08:rm=-0.03:bm=0.05",
    "vivid": "eq=saturation=1.4:contrast=1.06",
    "cinematic": "curves=preset=medium_contrast,colorbalance=rs=-0.05:bs=0.06:rh=0.06:bh=-0.05,eq=saturation=0.88",
    "vintage": "curves=preset=vintage,eq=saturation=0.85",
    "black & white": "hue=s=0,eq=contrast=1.12",
}


def look_filter(look: Look) -> str:
    parts = [_LOOKS[look.name]] if look.name in _LOOKS else []
    if look.brightness or look.contrast or look.saturation:
        parts.append(f"eq=brightness={0.15 * look.brightness:.3f}:contrast={1 + 0.3 * look.contrast:.3f}:"
                     f"saturation={max(0.0, 1 + 0.6 * look.saturation):.3f}")
    return ",".join(parts)


def _atempo(speed: float) -> str:
    """atempo only accepts 0.5-2.0 per instance, so chain it."""
    parts = []
    while speed > 2.0:
        parts.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        parts.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1.0) > 1e-3:
        parts.append(f"atempo={speed:.4f}")
    return ",".join(parts)


# ---- captions & text (ASS / SRT) --------------------------------------------

_STYLES = {
    # name: (font scale, primary, secondary, outline, back, border style, outline px, bold, max words)
    "classic": (0.060, "&H00FFFFFF", "&H00FFFFFF", "&H00000000", "&H80000000", 1, 3, -1, 6),
    "bold": (0.085, "&H0000FFFF", "&H0000FFFF", "&H00000000", "&H00000000", 1, 6, -1, 3),
    # Karaoke: words start white (secondary) and turn yellow (primary) as they're spoken.
    "karaoke": (0.075, "&H0000E1FF", "&H00FFFFFF", "&H00000000", "&H00000000", 1, 6, -1, 4),
    "minimal": (0.045, "&H00FFFFFF", "&H00FFFFFF", "&H00000000", "&H99000000", 3, 8, 0, 7),
}
_ALIGN = {"bottom": 2, "center": 5, "top": 8}


def place_words(project: Project, words_by_media: dict[str, list[Word]]) -> list[tuple[float, float, str]]:
    """Map source-time words onto the output timeline, dropping words that were cut."""
    placed: list[tuple[float, float, str]] = []
    for clip, off in zip(project.clips, project.offsets()):
        for w in words_by_media.get(clip.media_id, []):
            mid = (w.start + w.end) / 2
            if clip.start <= mid < clip.end:
                s = off + (max(w.start, clip.start) - clip.start) / clip.speed
                e = off + (min(w.end, clip.end) - clip.start) / clip.speed
                placed.append((s, e, w.text))
    return placed


Group = tuple[float, float, list[tuple[float, float, str]]]


def caption_groups(project: Project, words_by_media: dict[str, list[Word]], max_words: int,
                   max_len: float = 2.6) -> list[Group]:
    """Group the edit's spoken words into short on-screen caption lines."""
    groups: list[list[tuple[float, float, str]]] = []
    cur: list[tuple[float, float, str]] = []
    for word in place_words(project, words_by_media):
        if cur and (len(cur) >= max_words or word[1] - cur[0][0] > max_len or word[0] - cur[-1][1] > 0.7):
            groups.append(cur)
            cur = []
        cur.append(word)
    if cur:
        groups.append(cur)
    # Hold each line until the next one starts (up to 0.5s) so captions don't flicker.
    held: list[Group] = []
    for i, g in enumerate(groups):
        s, e = g[0][0], g[-1][1]
        nxt = groups[i + 1][0][0] if i + 1 < len(groups) else e + 0.5
        held.append((s, max(e, min(nxt, e + 0.5)), g))
    return held


def caption_lines(project: Project, words_by_media: dict[str, list[Word]], max_words: int,
                  max_len: float = 2.6) -> list[tuple[float, float, str]]:
    return [(s, e, " ".join(t for _, _, t in g)) for s, e, g in caption_groups(project, words_by_media, max_words, max_len)]


def _karaoke(group: list[tuple[float, float, str]], line_end: float) -> str:
    out = []
    for i, (ws, we, text) in enumerate(group):
        nxt = group[i + 1][0] if i + 1 < len(group) else max(we, line_end - 0.05)
        out.append("{\\k%d}%s" % (max(1, round((nxt - ws) * 100)), _escape(text)))
    return " ".join(out)


def build_ass(project: Project, words_by_media: dict[str, list[Word]], w: int, h: int) -> str | None:
    cap, texts = project.captions, project.texts
    if not (cap.enabled or texts):
        return None
    base = min(w, h)
    scale, prim, sec, outl, back, border, outline, bold, max_words = _STYLES[cap.style]
    margin_v = int(h * 0.09)
    styles = [
        f"Style: Cap,Arial,{int(base * scale)},{prim},{sec},{outl},{back},{bold},0,0,0,100,100,0,0,"
        f"{border},{max(1, int(outline * base / 1080))},0,{_ALIGN[cap.position]},40,40,{margin_v},1",
        f"Style: Title,Arial,{int(base * 0.075)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,"
        f"1,{max(1, int(4 * base / 1080))},1,8,40,40,{margin_v},1",
    ]
    events = []
    if cap.enabled:
        for s, e, group in caption_groups(project, words_by_media, max_words):
            if cap.style == "karaoke":
                events.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{_karaoke(group, e)}")
                continue
            text = " ".join(t for _, _, t in group)
            events.append(_dialogue(s, e, "Cap", text.upper() if cap.style == "bold" else text))
    for t in texts:
        style_override = "" if t.position == "top" else "{\\an%d}" % _ALIGN[t.position]
        events.append(_dialogue(t.start, t.end, "Title", t.text, style_override))
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, "",
    ])


def build_srt(project: Project, words_by_media: dict[str, list[Word]]) -> str:
    """Standard subtitle file: for YouTube uploads, players and screen readers."""
    out = []
    for i, (s, e, text) in enumerate(caption_lines(project, words_by_media, max_words=8, max_len=3.5), 1):
        out.append(f"{i}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{text}\n")
    return "\n".join(out)


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _dialogue(s: float, e: float, style: str, text: str, prefix: str = "") -> str:
    return f"Dialogue: 0,{_ts(s)},{_ts(e)},{style},,0,0,0,,{prefix}{_escape(text)}"


def _ts(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---- command construction -------------------------------------------------


def sources_for(project: Project) -> list[Source]:
    out = []
    n = len(project.clips)
    for i, c in enumerate(project.clips):
        m = project.media[c.media_id]
        fi = fo = 0.0
        if project.transition == "fade":
            fi = 0.25 if i > 0 else 0.0
            fo = 0.25 if i < n - 1 else 0.0
        if project.fade_ends:
            fi = 0.6 if i == 0 else fi
            fo = 0.8 if i == n - 1 else fo
        half = c.out_length / 2
        out.append(Source(m.path, c.start, c.length, m.has_audio, kind=m.kind,
                          speed=1.0 if m.kind == "image" else c.speed, zoom=c.zoom,
                          src_w=m.width, src_h=m.height, focus=m.focus, fade_in=min(fi, half), fade_out=min(fo, half),
                          zooms=[z for z in c.zooms if z[1] > c.start and z[0] < c.end] if m.kind == "video" else None))
    return out


def to_canvas(cx: float, cy: float, src: Source, w: int, h: int, fit: str) -> tuple[float, float]:
    """Map a point in the source frame onto the fitted w*h canvas (both 0-1)."""
    if not src.src_w or fit == "smart":
        return 0.5, 0.5
    s = max(w / src.src_w, h / src.src_h) if fit == "crop" else min(w / src.src_w, h / src.src_h)
    px = (w - src.src_w * s) / 2 + cx * src.src_w * s
    py = (h - src.src_h * s) / 2 + cy * src.src_h * s
    return min(max(px / w, 0.0), 1.0), min(max(py / h, 0.0), 1.0)


def punch_in_filter(src: Source, w: int, h: int, fit: str) -> str:
    """Animated zooms (smoothstep in over 0.18s, out over 0.3s) aimed at the action, plus flashes.

    Runs on a 2x-upscaled canvas so the zoom glides instead of stepping a pixel at a time.
    Times are clip-relative source seconds; ``it`` is the input frame time.
    """
    expr, cx_expr, cy_expr, flashes = ["st(1,0)"], "0.5", "0.5", []
    for t0, t1, amount, cx, cy, flash in src.zooms:
        a, b = t0 - src.start, t1 - src.start
        expr.append(f"st(0,clip((it-{a:.3f})/0.18,0,1)*clip(({b:.3f}-it)/0.3,0,1))")
        expr.append(f"st(1,max(ld(1),{amount - 1:.3f}*ld(0)*ld(0)*(3-2*ld(0))))")
        px, py = to_canvas(cx, cy, src, w, h, fit)
        cx_expr = f"if(between(it,{a - 0.2:.3f},{b + 0.4:.3f}),{px:.3f},{cx_expr})"
        cy_expr = f"if(between(it,{a - 0.2:.3f},{b + 0.4:.3f}),{py:.3f},{cy_expr})"
        if flash:
            flashes.append(f"0.32*max(0,1-abs(t-{a + 0.35:.3f})/0.09)")
    expr.append("1+ld(1)")
    z = ";".join(expr)
    chain = (f"fps={FPS},setpts=PTS-STARTPTS,scale={w * 2}:{h * 2},"
             f"zoompan=z='{z}':x='max(0,min(iw-iw/zoom,{cx_expr}*iw-iw/zoom/2))'"
             f":y='max(0,min(ih-ih/zoom,{cy_expr}*ih-ih/zoom/2))':d=1:s={w}x{h}:fps={FPS},setsar=1")
    if flashes:
        chain += f",eq=brightness='{'+'.join(flashes)}':eval=frame"
    return chain


def _video_chain(i: int, src: Source, w: int, h: int, fit: str, look: str) -> str:
    if src.kind == "rendered":
        return f"[{i}:v]setsar=1,setpts=PTS-STARTPTS[v{i}]"
    chain = (fit == "smart" and smart_filter(src, w, h)) or fit_filter(fit, w, h, str(i))
    if src.kind == "image":
        # Photos get a slow, gentle push-in so slideshows don't feel static.
        frames = max(1, round(src.out_length * FPS))
        chain += (f",zoompan=z='1+0.08*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d={frames}:s={w}x{h}:fps={FPS}")
    if src.zooms:
        chain += "," + punch_in_filter(src, w, h, fit)
    if src.zoom != 1.0:
        chain += f",crop=iw/{src.zoom:.3f}:ih/{src.zoom:.3f},scale={w}:{h}"
    chain += f",setpts=(PTS-STARTPTS)/{src.speed:.4f},fps={FPS}"
    if look:
        chain += "," + look
    if src.fade_in:
        chain += f",fade=t=in:st=0:d={src.fade_in:.3f}"
    if src.fade_out:
        chain += f",fade=t=out:st={src.out_length - src.fade_out:.3f}:d={src.fade_out:.3f}"
    return f"[{i}:v]{chain},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[v{i}]"


def _audio_chain(i: int, src: Source) -> str:
    out = f"{src.out_length:.3f}"
    if not src.has_audio:
        return f"anullsrc=r=48000:cl=stereo,atrim=0:{out}[a{i}]"
    chain = f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS"
    if tempo := _atempo(src.speed):
        chain += "," + tempo
    chain += f",apad,atrim=0:{out}"
    if src.fade_in:
        chain += f",afade=t=in:st=0:d={src.fade_in:.3f}"
    if src.fade_out:
        chain += f",afade=t=out:st={src.out_length - src.fade_out:.3f}:d={src.fade_out:.3f}"
    return chain + f"[a{i}]"


def build_command(sources: list[Source], w: int, h: int, fit: str, quality: Quality, out: Path,
                  final: Final | None = None, look: str = "") -> tuple[list[str], str]:
    """Return (ffmpeg args, filter graph). The graph is passed via a file."""
    args = [ffmpeg_bin(), "-y", "-hide_banner", "-nostats", "-nostdin", "-progress", "pipe:1"]
    graph: list[str] = []
    for i, src in enumerate(sources):
        if src.kind == "image":
            args += ["-i", src.path]
        else:
            args += ["-ss", f"{src.start:.3f}", "-t", f"{src.length:.3f}", "-i", src.path]
        graph.append(_video_chain(i, src, w, h, fit, look))
        graph.append(_audio_chain(i, src))
    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(sources)))
    graph.append(f"{pairs}concat=n={len(sources)}:v=1:a=1[vc][ac]")
    vout, aout = "vc", "ac"
    if final:
        if final.subtitles:
            graph.append(f"[vc]subtitles={final.subtitles}[vs]")
            vout = "vs"
        voice = []
        if final.clean_voice:
            # Rumble filter, adaptive noise reduction, presence boost, gentle leveling.
            voice.append("highpass=f=80,afftdn=nf=-25:tn=1,equalizer=f=3000:t=q:w=1.2:g=2.5,"
                         "acompressor=threshold=-20dB:ratio=3:attack=10:release=150")
        if abs(final.voice_volume - 1.0) > 1e-3:
            voice.append(f"volume={final.voice_volume:.2f}")
        if voice:
            graph.append(f"[ac]{','.join(voice)}[vx]")
            aout = "vx"
        if final.music:
            mpath, vol, duck = final.music
            m = len(sources)
            args += ["-stream_loop", "-1", "-i", mpath]
            fade = max(final.total - 1.5, 0)
            graph.append(f"[{m}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol:.2f},"
                         f"atrim=0:{final.total:.3f},afade=t=out:st={fade:.3f}:d=1.5[mus]")
            if duck:
                # The voice drives a compressor on the music: music dips while anyone talks.
                graph.append(f"[{aout}]asplit=2[vo][sc]")
                graph.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[md]")
                graph.append("[vo][md]amix=inputs=2:duration=first:normalize=0[am]")
            else:
                graph.append(f"[{aout}][mus]amix=inputs=2:duration=first:normalize=0[am]")
            aout = "am"
        if final.normalize:
            graph.append(f"[{aout}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[an]")
            aout = "an"
    args += ["-/filter_complex", "graph.txt", "-map", f"[{vout}]", "-map", f"[{aout}]",
             "-c:v", "libx264", "-preset", quality.preset, "-crf", str(quality.crf),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    return args, ";\n".join(graph)


def render(project: Project, out: Path, quality: Quality, words_by_media: dict[str, list[Word]],
           on_progress: Callable[[float], None] | None = None) -> Path:
    if not project.clips:
        raise ValueError("The timeline is empty - import a video first.")
    w, h = output_size(project, quality)
    total = project.duration
    sources = sources_for(project)
    look = look_filter(project.look)
    music = None
    if project.music:
        music = (project.media[project.music.media_id].path, project.music.volume, project.music.duck)
    a = project.audio

    with tempfile.TemporaryDirectory(prefix="clipchat-") as tmp:
        work = Path(tmp)
        fit = project.fit
        # Long timelines (e.g. after removing silences) are rendered in chunks first.
        if len(sources) > CHUNK:
            chunks = []
            groups = [sources[i:i + CHUNK] for i in range(0, len(sources), CHUNK)]
            done = 0.0
            for gi, group in enumerate(groups):
                part = work / f"part{gi}.mp4"
                q = Quality(None, quality.preset, min(quality.crf, INTERMEDIATE_CRF) if quality.max_side is None
                            else quality.crf)
                args, graph = build_command(group, w, h, fit, q, part, look=look)
                length = sum(s.out_length for s in group)
                _run(args, graph, work, length,
                     lambda p, d=done, L=length: on_progress and on_progress(0.6 * (d + p * L) / total))
                done += length
                chunks.append(Source(str(part), 0.0, length, True, kind="rendered"))
            sources, look = chunks, ""  # chunks already have per-clip effects baked in
            base, span = 0.6, 0.4
        else:
            base, span = 0.0, 1.0
        ass = build_ass(project, words_by_media, w, h)
        if ass:
            (work / "subs.ass").write_text(ass, encoding="utf-8")
        final = Final(total, "subs.ass" if ass else None, music, a.clean_voice, a.voice_volume, a.normalize)
        args, graph = build_command(sources, w, h, fit, quality, out.resolve(), final, look)
        _run(args, graph, work, total, lambda p: on_progress and on_progress(base + span * p))
    if on_progress:
        on_progress(1.0)
    return out


def _run(args: list[str], graph: str, cwd: Path, total: float, on_progress: Callable[[float], None]) -> None:
    (cwd / "graph.txt").write_text(graph, encoding="utf-8")
    proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    stderr_tail: list[str] = []

    def drain():
        for line in proc.stderr:
            stderr_tail.append(line)
            del stderr_tail[:-30]

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    for line in proc.stdout:
        if line.startswith("out_time_us=") and total > 0:
            try:
                on_progress(min(int(line.split("=")[1]) / 1e6 / total, 0.999))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg failed:\n" + "".join(stderr_tail[-12:]))
