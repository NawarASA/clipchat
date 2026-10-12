import { Maximize, Pause, Play } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { fmt, mediaUrl } from "./api.js";
import Orb from "./Orb.jsx";

// Plays the low-res preview the engine re-renders after every edit.
export default function Preview({ snap }) {
  const video = useRef();
  const box = useRef();
  const resumeAt = useRef(0);
  const [shown, setShown] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const p = snap.preview;

  // Swap in a finished render, keeping the playhead where it was.
  useEffect(() => {
    if (!p.rendering && !p.error && !p.empty && p.version >= 0 && p.version !== shown) {
      resumeAt.current = video.current?.currentTime || 0;
      setShown(p.version);
    }
  }, [p.rendering, p.error, p.empty, p.version, shown]);

  const toggle = () => {
    const v = video.current;
    if (!v) return;
    v.paused ? v.play() : v.pause();
  };
  const seek = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (video.current && dur) video.current.currentTime = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1) * dur;
  };
  const onKey = (e) => {
    if (e.key === " " || e.key === "k") { e.preventDefault(); toggle(); }
    if (e.key === "ArrowRight" && video.current) video.current.currentTime += 5;
    if (e.key === "ArrowLeft" && video.current) video.current.currentTime -= 5;
  };

  const stale = p.rendering || p.version !== snap.version;
  const pct = dur ? (time / dur) * 100 : 0;

  return (
    <div ref={box} className={`player ${playing ? "" : "paused"}`} tabIndex={0} onKeyDown={onKey} aria-label="Video preview">
      {shown !== null ? (
        <video
          ref={video}
          key={shown}
          src={mediaUrl(`/preview.mp4?v=${shown}`)}
          onClick={toggle}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => setTime(e.target.currentTime)}
          onLoadedMetadata={(e) => {
            setDur(e.target.duration || 0);
            e.target.currentTime = Math.min(resumeAt.current, e.target.duration || 0);
          }}
        />
      ) : (
        <div className="player-empty">Preparing your preview…</div>
      )}

      {shown !== null && (
        <>
          <button className={`big-play ${playing ? "hidden" : ""}`} onClick={toggle} aria-label="Play"><Play size={30} fill="#fff" style={{ marginLeft: 4 }} /></button>
          <div className="controls">
            <button className="circle" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
              {playing ? <Pause size={17} fill="#fff" /> : <Play size={17} fill="#fff" />}
            </button>
            <span className="time">{fmt(time)}</span>
            <div className="scrub" onClick={seek} role="slider" aria-label="Seek" aria-valuemin={0}
                 aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(time)}>
              <div className="scrub-track">
                <div className="scrub-fill" style={{ width: `${pct}%` }} />
                <div className="scrub-knob" style={{ left: `${pct}%` }} />
              </div>
            </div>
            <span className="time">{fmt(dur)}</span>
            <button className="circle" aria-label="Full screen" onClick={() => box.current?.requestFullscreen?.()}><Maximize size={16} /></button>
          </div>
        </>
      )}

      {stale && !p.error && (
        <div className="pill-status" role="status">
          <Orb size={26} state="thinking" />
          {p.rendering ? `Updating preview · ${Math.round(p.progress * 100)}%` : "Updating preview…"}
        </div>
      )}
      {snap.status && !stale && <div className="pill-status" role="status"><Orb size={26} state="thinking" />{snap.status}</div>}
      {p.error && <div className="pill-status error" title={p.error}>Preview failed: {p.error.split("\n").slice(-2).join(" ")}</div>}
    </div>
  );
}
