import { Download, FolderOpen } from "lucide-react";
import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import Orb from "./Orb.jsx";

export default function ExportDialog({ snap, onClose, notify }) {
  const [resolution, setResolution] = useState("full");
  const [quality, setQuality] = useState("high");
  const [subtitles, setSubtitles] = useState(true);
  const [job, setJob] = useState(null);

  useEffect(() => {
    if (job?.state !== "running") return;
    const id = setInterval(async () => {
      try {
        const j = await api.job(job.id);
        setJob({ ...j, id: job.id });
        if (j.state === "error") notify(`Export failed: ${j.error}`);
      } catch { /* try again */ }
    }, 400);
    return () => clearInterval(id);
  }, [job, notify]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && job?.state !== "running") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [job, onClose]);

  const start = async () => {
    const path = await window.clipchat.saveFile();
    if (!path) return;
    try {
      const r = await api.exportTo({ path, resolution, quality, subtitles });
      setJob({ id: r.job, state: "running", progress: 0, path });
    } catch (e) {
      notify(e.message);
    }
  };

  const running = job?.state === "running";
  return (
    <div className="backdrop" onClick={() => !running && onClose()}>
      <div className="modal glass" role="dialog" aria-modal="true" aria-labelledby="export-title" onClick={(e) => e.stopPropagation()}>
        {job?.state === "done" ? (
          <div className="export-done">
            <Orb size={84} />
            <h2 id="export-title">Your video is ready</h2>
            <p className="sub">{job.path}{job.subtitles ? " (plus a subtitle file)" : ""}</p>
            <div className="modal-actions" style={{ justifyContent: "center" }}>
              <button className="pill" onClick={onClose}>Close</button>
              <button className="pill pill-primary" onClick={() => window.clipchat.showInFolder(job.path)}><FolderOpen size={16} />Show in folder</button>
            </div>
          </div>
        ) : running ? (
          <>
            <div className="export-orb"><Orb size={72} state="thinking" /></div>
            <h2 id="export-title" style={{ textAlign: "center" }}>Exporting…</h2>
            <p className="sub">Rendering at full quality. You can keep this window open.</p>
            <div className="big-progress" role="progressbar" aria-valuenow={Math.round(job.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
              <div style={{ width: `${Math.max(job.progress * 100, 3)}%` }} />
            </div>
            <p className="muted" style={{ margin: 0, fontWeight: 700 }}>{Math.round(job.progress * 100)}%</p>
          </>
        ) : (
          <>
            <h2 id="export-title">Export video</h2>
            <p className="sub">Saves an MP4 that plays everywhere.</p>
            <h4>Resolution</h4>
            <div className="seg full">
              {[["full", "Best"], ["1080p", "1080p"], ["720p", "720p"]].map(([k, l]) => (
                <button key={k} className={resolution === k ? "on" : ""} onClick={() => setResolution(k)}>{l}</button>
              ))}
            </div>
            <h4>Quality</h4>
            <div className="seg full">
              {[["high", "High"], ["standard", "Standard"], ["small", "Small file"]].map(([k, l]) => (
                <button key={k} className={quality === k ? "on" : ""} onClick={() => setQuality(k)}>{l}</button>
              ))}
            </div>
            <div className="row mt">
              <label htmlFor="srt">Also save subtitles (.srt)<span className="hint">For YouTube, players and screen readers</span></label>
              <button id="srt" type="button" role="switch" aria-checked={subtitles} className={`toggle ${subtitles ? "on" : ""}`}
                      onClick={() => setSubtitles(!subtitles)} />
            </div>
            <div className="modal-actions">
              <button className="pill" onClick={onClose}>Cancel</button>
              <button className="pill pill-primary" onClick={start} disabled={!snap.project.clips.length}><Download size={16} />Choose where to save</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
