import { Captions, Image as ImageIcon, Music, Plus, Sparkles } from "lucide-react";
import React, { useState } from "react";
import { api, fmt, mediaUrl } from "./api.js";

const SHAPE = { original: "Original", "9:16": "Vertical", "1:1": "Square", "16:9": "Widescreen" };

// The timeline as a row of glass bubbles: click to select, drag to reorder.
export default function Timeline({ snap, run, edit, selected, setSelected, onImport }) {
  const { project, duration } = snap;
  const [dragFrom, setDragFrom] = useState(null);
  const [dropAt, setDropAt] = useState(null);
  const media = project.media;

  const drop = (to) => {
    if (dragFrom !== null && dragFrom !== to) {
      run(() => api.edit("move_clip", { clip: dragFrom, to_position: to }));
      setSelected(to);
    }
    setDragFrom(null);
    setDropAt(null);
  };

  const onKey = (e, n) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(selected === n ? null : n); }
    if (e.key === "Delete" || e.key === "Backspace") edit("delete_clip", { clip: n });
    if (e.key === "ArrowLeft" && n > 1 && e.altKey) { edit("move_clip", { clip: n, to_position: n - 1 }); setSelected(n - 1); }
    if (e.key === "ArrowRight" && n < project.clips.length && e.altKey) { edit("move_clip", { clip: n, to_position: n + 1 }); setSelected(n + 1); }
  };

  return (
    <div className="glass dock">
      <div className="dock-head">
        <span className="dock-title">Timeline</span>
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          {project.clips.length} clip{project.clips.length === 1 ? "" : "s"} · {fmt(duration)}
        </span>
        <div className="dock-chips">
          <span className="chip chip-glass">{SHAPE[project.aspect]}{project.fit === "smart" && project.aspect !== "original" ? " · follows face" : ""}</span>
          {project.captions.enabled && <span className="chip chip-glass"><Captions size={13} />{project.captions.style}</span>}
          {project.look.name !== "none" && <span className="chip chip-glass"><Sparkles size={13} />{project.look.name}</span>}
          {project.music && <span className="chip chip-glass"><Music size={13} />{media[project.music.media_id]?.name}</span>}
        </div>
      </div>

      <div className="strip" onDragOver={(e) => e.preventDefault()} role="listbox" aria-label="Clips">
        {project.clips.map((c, i) => {
          const n = i + 1;
          const m = media[c.media_id];
          return (
            <div
              key={c.id}
              role="option"
              aria-selected={selected === n}
              aria-label={`Clip ${n}, ${m?.name}`}
              tabIndex={0}
              className={`bubble-clip ${selected === n ? "selected" : ""} ${dragFrom === n ? "dragging" : ""} ${dropAt === n ? "drop-target" : ""}`}
              draggable
              onClick={() => setSelected(selected === n ? null : n)}
              onKeyDown={(e) => onKey(e, n)}
              onDragStart={(e) => { e.stopPropagation(); e.dataTransfer.effectAllowed = "move"; setDragFrom(n); }}
              onDragEnter={() => dragFrom !== null && setDropAt(n)}
              onDragEnd={() => { setDragFrom(null); setDropAt(null); }}
              onDrop={(e) => { if (dragFrom !== null) { e.preventDefault(); e.stopPropagation(); drop(n); } }}
              title={`${m?.name}\n${m?.kind === "image" ? "Photo" : `${fmt(c.start)}–${fmt(c.end)} of the original`}`}
            >
              <span className="disc" style={{ backgroundImage: `url(${mediaUrl(`/thumb/${c.media_id}`)})` }}>
                <span className="disc-num">{n}</span>
                {(c.speed !== 1 || c.zoom !== 1 || c.zooms.some((z) => z[1] > c.start && z[0] < c.end)) && (
                  <span className="disc-tag">
                    {c.speed !== 1 ? `${c.speed}×` : c.zooms.some((z) => z[1] > c.start && z[0] < c.end) ? "⚡" : "zoom"}
                  </span>
                )}
              </span>
              <span className="len">
                {m?.kind === "image" && <ImageIcon size={11} />}
                {((c.end - c.start) / c.speed).toFixed(1)}s
              </span>
            </div>
          );
        })}
        <button className="bubble-clip bubble-add" onClick={onImport} aria-label="Add media">
          <span className="disc"><Plus size={22} /></span>
          <span className="len">Add</span>
        </button>
      </div>

      {Object.keys(media).length > 0 && (
        <div className="files">
          {Object.values(media).map((m) => (
            <span key={m.id} className="file chip-glass">
              {m.kind === "audio" ? <Music size={13} /> : m.kind === "image" ? <ImageIcon size={13} /> : null}
              {m.name}
              {m.kind !== "audio" && <button onClick={() => edit("add_clip", { file: m.name })} title="Add again at the end">Add</button>}
              {m.kind === "audio" && project.music?.media_id !== m.id && (
                <button onClick={() => edit("set_music", { file: m.name, volume: 0.25, duck: true })}>Use as music</button>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
