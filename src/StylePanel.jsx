import { AudioLines, Blend, Captions, Clapperboard, Film, Palette, RectangleHorizontal, Trash2, WandSparkles, Zap, ZoomIn } from "lucide-react";
import React, { useEffect, useState } from "react";
import RecipeIcon from "./RecipeIcon.jsx";

const SHAPES = [
  { key: "original", label: "Original", w: 20, h: 13 },
  { key: "9:16", label: "Vertical", w: 11, h: 19 },
  { key: "1:1", label: "Square", w: 15, h: 15 },
  { key: "16:9", label: "Wide", w: 22, h: 12.5 },
];
const FITS = [["smart", "Face"], ["blur", "Blur"], ["crop", "Fill"], ["pad", "Bars"]];
const CAP_BG = "#1c1c20";
const CAPTIONS = [
  { key: "classic", label: "Classic", sample: <span className="cap-sample" style={{ textShadow: "0 0 3px #000" }}>Aa</span> },
  { key: "bold", label: "Bold", sample: <span className="cap-sample" style={{ color: "#ffe14d", fontSize: "0.95rem" }}>AA</span> },
  { key: "karaoke", label: "Karaoke", sample: <span className="cap-sample"><span style={{ color: "#ffe14d" }}>Hi</span> yo</span> },
  { key: "minimal", label: "Minimal", sample: <span className="cap-sample" style={{ background: "rgba(0,0,0,.55)", padding: "1px 5px", borderRadius: 6, fontWeight: 600 }}>Aa</span> },
];
const LOOKS = [
  ["none", "Natural", "radial-gradient(circle at 30% 30%, #cfe3ff, #8fb4ff 60%, #ffd2a8)"],
  ["warm", "Warm", "radial-gradient(circle at 30% 30%, #ffd59a, #ff8a5c 60%, #e2553f)"],
  ["cool", "Cool", "radial-gradient(circle at 30% 30%, #b8ecff, #5aa8ff 60%, #5c5cff)"],
  ["vivid", "Vivid", "conic-gradient(from 200deg, #ff4fa0, #ffd23f, #2fe0a8, #4f8bff, #ff4fa0)"],
  ["cinematic", "Cinema", "radial-gradient(circle at 30% 30%, #f6b26b, #2b6f78 70%)"],
  ["vintage", "Vintage", "radial-gradient(circle at 30% 30%, #ecd2a8, #a8865e 60%, #6d5440)"],
  ["black & white", "Mono", "radial-gradient(circle at 30% 30%, #ffffff, #9a9a9a 55%, #2c2c2c)"],
];
const SPEEDS = [0.5, 1, 1.5, 2, 3];

export default function StylePanel({ snap, edit, recipes, runRecipe, busy, selected, setSelected }) {
  const p = snap.project;
  const [intensity, setIntensity] = useState("normal");
  const [reelLength, setReelLength] = useState(30);
  const zoomCount = p.clips.reduce((n, c) => n + c.zooms.filter((z) => z[1] > c.start && z[0] < c.end).length, 0);
  const has = p.clips.length > 0;
  const audioFiles = Object.values(p.media).filter((m) => m.kind === "audio");
  const look = p.look;
  const setLook = (changes) => edit("set_look", { look: look.name, brightness: look.brightness, contrast: look.contrast, saturation: look.saturation, ...changes });
  const setAudio = (changes) => edit("set_audio", { ...p.audio, ...changes });
  const setMusic = (changes) => p.music && edit("set_music", { file: p.media[p.music.media_id].name, volume: p.music.volume, duck: p.music.duck, ...changes });
  const setCaptions = (changes) => edit("set_captions", { ...p.captions, ...changes });
  const clip = selected ? p.clips[selected - 1] : null;
  const clipMedia = clip ? p.media[clip.media_id] : null;

  return (
    <div className="style-scroll">
      {clip && (
        <section className="section chip-glass" style={{ boxShadow: "inset 0 0 0 1.5px var(--ink-3)" }}>
          <h3><Clapperboard size={15} /><span className="grow">Clip {selected}</span>
            <button className="pill pill-plain" style={{ height: "1.9rem", padding: "0 0.75rem" }} onClick={() => setSelected(null)}>Done</button>
          </h3>
          {clipMedia.kind === "image" ? (
            <div className="row">
              <span className="label">On screen for</span>
              <Slider value={clip.end - clip.start} min={1} max={15} step={0.5} format={(v) => `${v}s`}
                      onCommit={(v) => edit("set_photo_length", { clip: selected, seconds: v })} />
            </div>
          ) : (
            <div className="row">
              <span className="label">Speed</span>
              <div className="speed-row" role="radiogroup" aria-label="Speed">
                {SPEEDS.map((s) => (
                  <button key={s} role="radio" aria-checked={clip.speed === s} className={`speed ${clip.speed === s ? "on" : ""}`}
                          onClick={() => edit("set_speed", { clip: selected, speed: s })}>{s}×</button>
                ))}
              </div>
            </div>
          )}
          <div className="row">
            <span className="label">Zoom in</span>
            <Slider value={clip.zoom} min={1} max={2} step={0.05} format={(v) => `${v.toFixed(2)}×`}
                    onCommit={(v) => edit("set_zoom", { clip: selected, zoom: v })} />
          </div>
          <div className="row mt">
            <button className="pill" style={{ color: "var(--bad)" }} onClick={() => { edit("delete_clip", { clip: selected }); setSelected(null); }}>
              <Trash2 size={15} />Delete clip
            </button>
          </div>
        </section>
      )}

      <section className="section chip-glass">
        <h3><WandSparkles size={15} />Magic presets</h3>
        <div className="orbs">
          {recipes.map((r) => (
            <button key={r.key} className="orb-btn" disabled={!has || busy} onClick={() => runRecipe(r.key)} title={r.description}>
              <span className="ball"><RecipeIcon name={r.key} size={19} /></span>
              {r.label}
            </button>
          ))}
        </div>
        {!has && <p className="empty-hint" style={{ margin: "0.8rem 0 0", textAlign: "center" }}>Add a video to use presets.</p>}
      </section>

      <section className="section chip-glass">
        <h3><Zap size={15} /><span className="grow">Highlights</span>
          {zoomCount > 0 && <span className="chip chip-glass" style={{ height: "1.5rem" }}>{zoomCount} zoom{zoomCount > 1 ? "s" : ""}</span>}
        </h3>
        <p className="empty-hint" style={{ margin: "-0.3rem 0 0.8rem" }}>
          Finds the kills, fights and big reactions, then punches in on them. Great for gaming and sports.
        </p>
        <div className="seg full" role="radiogroup" aria-label="Highlight intensity">
          {[["subtle", "Subtle"], ["normal", "Normal"], ["hype", "Hype"]].map(([k, l]) => (
            <button key={k} role="radio" aria-checked={intensity === k} className={intensity === k ? "on" : ""} onClick={() => setIntensity(k)}>{l}</button>
          ))}
        </div>
        <div className="orbs mt" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <button className="orb-btn" disabled={!has || busy}
                  onClick={() => edit("auto_highlights", { style: "zoom", intensity, target_seconds: reelLength })}>
            <span className="ball"><ZoomIn size={19} /></span>
            Zoom on the action
          </button>
          <button className="orb-btn" disabled={!has || busy}
                  onClick={() => edit("auto_highlights", { style: "reel", intensity, target_seconds: reelLength })}>
            <span className="ball"><Film size={19} /></span>
            Make a highlight reel
          </button>
        </div>
        <div className="row mt">
          <span className="label">Reel length</span>
          <Slider value={reelLength} min={10} max={90} step={5} format={(v) => `${v}s`} disabled={!has} onCommit={setReelLength} />
        </div>
        {zoomCount > 0 && (
          <div className="row">
            <button className="pill pill-plain" style={{ height: "2rem" }} onClick={() => edit("remove_zooms", {})}>Remove all zooms</button>
          </div>
        )}
      </section>

      <section className="section chip-glass">
        <h3><RectangleHorizontal size={15} />Shape</h3>
        <div className="orbs four" role="radiogroup" aria-label="Video shape">
          {SHAPES.map((s) => (
            <button key={s.key} role="radio" aria-checked={p.aspect === s.key} className={`orb-btn small ${p.aspect === s.key ? "on" : ""}`}
                    disabled={!has} onClick={() => edit("set_format", { aspect: s.key, fit: p.fit })}>
              <span className="ball"><i className="shape-icon" style={{ width: s.w, height: s.h }} /></span>{s.label}
            </button>
          ))}
        </div>
        {p.aspect !== "original" && (
          <div className="seg full mt" role="radiogroup" aria-label="How to fill the shape">
            {FITS.map(([k, label]) => (
              <button key={k} role="radio" aria-checked={p.fit === k} className={p.fit === k ? "on" : ""}
                      onClick={() => edit("set_format", { aspect: p.aspect, fit: k })}>{label}</button>
            ))}
          </div>
        )}
      </section>

      <section className="section chip-glass">
        <h3><Captions size={15} /><span className="grow">Captions</span>
          <Toggle label="Captions" on={p.captions.enabled} disabled={!has} onChange={(v) => setCaptions({ enabled: v })} />
        </h3>
        <div className="orbs four" role="radiogroup" aria-label="Caption style">
          {CAPTIONS.map((c) => (
            <button key={c.key} role="radio" aria-checked={p.captions.style === c.key && p.captions.enabled}
                    className={`orb-btn small ${p.captions.enabled && p.captions.style === c.key ? "on" : ""}`}
                    disabled={!has} onClick={() => setCaptions({ enabled: true, style: c.key })}>
              <span className="ball swatch" style={{ background: CAP_BG }}>{c.sample}</span>{c.label}
            </button>
          ))}
        </div>
        {p.captions.enabled && (
          <div className="seg full mt" role="radiogroup" aria-label="Caption position">
            {["top", "center", "bottom"].map((pos) => (
              <button key={pos} className={p.captions.position === pos ? "on" : ""} onClick={() => setCaptions({ position: pos })}>
                {pos[0].toUpperCase() + pos.slice(1)}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="section chip-glass">
        <h3><Palette size={15} />Look</h3>
        <div className="orbs four" role="radiogroup" aria-label="Color look">
          {LOOKS.map(([k, label, bg]) => (
            <button key={k} role="radio" aria-checked={look.name === k} className={`orb-btn small ${look.name === k ? "on" : ""}`}
                    disabled={!has} onClick={() => setLook({ look: k })}>
              <span className="ball swatch" style={{ background: bg }} />{label}
            </button>
          ))}
        </div>
        <div className="mt">
          {["brightness", "contrast", "saturation"].map((k) => (
            <div className="row" key={k}>
              <span className="label">{k[0].toUpperCase() + k.slice(1)}</span>
              <Slider value={look[k]} min={-1} max={1} step={0.05} format={(v) => (v > 0 ? "+" : "") + Math.round(v * 100)}
                      disabled={!has} onCommit={(v) => setLook({ [k]: v })} />
            </div>
          ))}
        </div>
      </section>

      <section className="section chip-glass">
        <h3><AudioLines size={15} />Sound</h3>
        <div className="row">
          <label htmlFor="t-voice">Studio voice<span className="hint">Removes background noise, makes speech clearer</span></label>
          <Toggle id="t-voice" on={p.audio.clean_voice} disabled={!has} onChange={(v) => setAudio({ clean_voice: v })} />
        </div>
        <div className="row">
          <label htmlFor="t-norm">Even out loudness<span className="hint">As loud as other social videos</span></label>
          <Toggle id="t-norm" on={p.audio.normalize} disabled={!has} onChange={(v) => setAudio({ normalize: v })} />
        </div>
        <div className="row">
          <span className="label">Voice volume</span>
          <Slider value={p.audio.voice_volume} min={0} max={2} step={0.05} format={(v) => `${Math.round(v * 100)}%`}
                  disabled={!has} onCommit={(v) => setAudio({ voice_volume: v })} />
        </div>
        <div className="row mt">
          <span className="label">Music</span>
          {audioFiles.length ? (
            <select className="select" value={p.music ? p.media[p.music.media_id].name : "none"} disabled={!has}
                    onChange={(e) => edit("set_music", { file: e.target.value, volume: p.music?.volume ?? 0.25, duck: p.music?.duck ?? true })}>
              <option value="none">No music</option>
              {audioFiles.map((m) => <option key={m.id} value={m.name}>{m.name}</option>)}
            </select>
          ) : <span className="empty-hint">Add an MP3 to use music</span>}
        </div>
        {p.music && (
          <>
            <div className="row">
              <span className="label">Music volume</span>
              <Slider value={p.music.volume} min={0} max={1} step={0.05} format={(v) => `${Math.round(v * 100)}%`}
                      onCommit={(v) => setMusic({ volume: v })} />
            </div>
            <div className="row">
              <label htmlFor="t-duck">Lower music when talking</label>
              <Toggle id="t-duck" on={p.music.duck} onChange={(v) => setMusic({ duck: v })} />
            </div>
          </>
        )}
      </section>

      <section className="section chip-glass">
        <h3><Blend size={15} />Transitions</h3>
        <div className="row">
          <label htmlFor="t-fade">Fade between clips</label>
          <Toggle id="t-fade" on={p.transition === "fade"} disabled={!has}
                  onChange={(v) => edit("set_transitions", { between_clips: v ? "fade" : "none", fade_in_out: p.fade_ends })} />
        </div>
        <div className="row">
          <label htmlFor="t-ends">Fade in & out at the ends</label>
          <Toggle id="t-ends" on={p.fade_ends} disabled={!has}
                  onChange={(v) => edit("set_transitions", { between_clips: p.transition, fade_in_out: v })} />
        </div>
      </section>
    </div>
  );
}

function Toggle({ on, onChange, disabled, id, label }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
            className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} />
  );
}

// Moves freely while dragging; sends one edit when let go (each edit re-renders the preview).
function Slider({ value, min, max, step, format, onCommit, disabled }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => { if (Math.abs(v - value) > 1e-6) onCommit(v); };
  return (
    <>
      <input className="slider" type="range" min={min} max={max} step={step} value={v} disabled={disabled}
             style={{ "--fill": `${((v - min) / (max - min)) * 100}%` }}
             onChange={(e) => setV(parseFloat(e.target.value))} onPointerUp={commit} onKeyUp={commit} />
      <span className="val">{format(v)}</span>
    </>
  );
}
