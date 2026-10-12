import { Clapperboard, Gamepad2, Images, Mic, MonitorPlay, Smartphone, Sparkles, Zap } from "lucide-react";
import React from "react";

// One quiet icon per preset.
const ICONS = {
  tiktok: Smartphone,
  youtube: MonitorPlay,
  podcast: Mic,
  highlights: Zap,
  gaming: Gamepad2,
  cinematic: Clapperboard,
  slideshow: Images,
  cleanup: Sparkles,
};

// small: a tiny circle for inline chips. Otherwise just the icon (the caller draws the circle).
export default function RecipeIcon({ name, size = 15, small }) {
  const Icon = ICONS[name] || Sparkles;
  if (!small) return <Icon size={size} strokeWidth={2} />;
  return (
    <span className="mini-orb" aria-hidden>
      <Icon size={size} strokeWidth={2.2} />
    </span>
  );
}
