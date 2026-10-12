import React from "react";

// The Siri screen-edge glow, shown only while the assistant is working.
export function SiriGlow({ active }) {
  return (
    <div className={`siri-glow ${active ? "on" : ""}`} aria-hidden>
      <div className="glow-band soft"><i /></div>
      <div className="glow-band sharp"><i /></div>
    </div>
  );
}
