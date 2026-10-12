import React, { useEffect, useState } from "react";

export default function Settings({ settings, theme, onSave, onClose }) {
  const [key, setKey] = useState("");
  const [claudeModel, setClaudeModel] = useState(settings.claude_model);
  const [ollamaModel, setOllamaModel] = useState(settings.ollama_model);
  const [ollamaHost, setOllamaHost] = useState(settings.ollama_host);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const save = async () => {
    const changes = { claude_model: claudeModel.trim(), ollama_model: ollamaModel.trim(), ollama_host: ollamaHost.trim() };
    if (key.trim()) changes.api_key = key.trim();
    await onSave(changes);
    onClose();
  };

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal glass" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="settings-title">Settings</h2>
        <p className="sub">Appearance and which AI does the editing.</p>

        <h4>Appearance</h4>
        <div className="row">
          <span className="label">Theme</span>
          <div className="seg">
            {[["system", "Auto"], ["light", "Light"], ["dark", "Dark"]].map(([k, l]) => (
              <button key={k} className={theme.pref === k ? "on" : ""} onClick={() => theme.setPref(k)}>{l}</button>
            ))}
          </div>
        </div>
        <div className="row">
          <span className="label">Text size</span>
          <div className="seg">
            {[["normal", "Normal"], ["large", "Large"]].map(([k, l]) => (
              <button key={k} className={theme.textSize === k ? "on" : ""} onClick={() => theme.setTextSize(k)}>{l}</button>
            ))}
          </div>
        </div>

        <h4>Claude (cloud, smartest)</h4>
        <label className="field">
          <span>API key {settings.has_api_key && <span className="muted">· saved</span>}</span>
          <input type="password" autoComplete="off" placeholder={settings.has_api_key ? "Leave blank to keep the current key" : "sk-ant-…"}
                 value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        <label className="field">
          <span>Model</span>
          <input value={claudeModel} onChange={(e) => setClaudeModel(e.target.value)} />
        </label>

        <h4>On-device (free, private)</h4>
        <label className="field">
          <span>Model</span>
          {settings.ollama_models.length ? (
            <select value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)}>
              {!settings.ollama_models.includes(ollamaModel) && <option>{ollamaModel}</option>}
              {settings.ollama_models.map((m) => <option key={m}>{m}</option>)}
            </select>
          ) : (
            <input value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} />
          )}
        </label>
        <p className="muted" style={{ fontSize: "0.8rem", margin: "-0.2rem 0 0.7rem" }}>
          Runs with Ollama on your computer. Use a model that supports tools, like llama3.1 or mistral-nemo.
          {settings.ollama_models.length === 0 && " Ollama doesn't seem to be running."}
        </p>
        <label className="field">
          <span>Ollama address</span>
          <input value={ollamaHost} onChange={(e) => setOllamaHost(e.target.value)} />
        </label>

        <div className="modal-actions">
          <button className="pill" onClick={onClose}>Cancel</button>
          <button className="pill pill-primary" onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}
