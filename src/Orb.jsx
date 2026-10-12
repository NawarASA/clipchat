import React, { forwardRef } from "react";

// The assistant's face: a softly churning, Siri-like orb.
// state: "idle" | "thinking" | "listening". --lvl (0-1) can be driven live (e.g. by mic volume).
const Orb = forwardRef(function Orb({ size = 56, state = "idle", className = "" }, ref) {
  return (
    <span ref={ref} className={`orb orb-${state} ${className}`} style={{ "--s": `${size}px` }} aria-hidden>
      <span className="orb-core">
        <i className="b1" /><i className="b2" /><i className="b3" /><i className="b4" />
      </span>
      <span className="orb-gloss" />
    </span>
  );
});

export default Orb;
