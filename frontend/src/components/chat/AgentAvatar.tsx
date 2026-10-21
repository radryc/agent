import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";

export type AvatarPersona = "otto" | "steve";

interface AgentAvatarProps {
  size?: "sm" | "md" | "lg";
  isTyping?: boolean;
  /** Render a static still frame instead of the (muted) video. */
  still?: boolean;
  /** Which persona's face to show (defaults to Otto). */
  persona?: AvatarPersona;
}

const sizes = {
  sm: "w-8 h-8 text-sm",
  md: "w-10 h-10 text-base",
  lg: "w-14 h-14 text-xl",
};

const ASSETS: Record<AvatarPersona, { video?: string; poster?: string; still?: string }> = {
  otto: {
    video: "/otto.mp4",
    poster: "/otto-poster.jpg",
    still: "/otto-avatar.jpg",
  },
  steve: {
    video: "/steve.mp4",
    poster: "/steve-avatar.jpg",
    still: "/steve-avatar.jpg",
  },
};

const PERSONA_STYLES: Record<AvatarPersona, string> = {
  otto: "from-accent-blue to-accent-purple",
  steve: "from-accent-purple to-pink-500",
};

const PERSONA_INITIALS: Record<AvatarPersona, string> = {
  otto: "O",
  steve: "S",
};

export default function AgentAvatar({
  size = "md",
  isTyping = false,
  still = false,
  persona = "otto",
}: AgentAvatarProps) {
  const [open, setOpen] = useState(false);
  const assets = ASSETS[persona] ?? ASSETS.otto;
  const hasVisuals = Boolean(assets.poster || assets.still || assets.video);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const baseClass = `relative flex items-center justify-center overflow-hidden rounded-full bg-gradient-to-br ${PERSONA_STYLES[persona]} font-bold text-white ${sizes[size]} ${
    isTyping ? "animate-glow-pulse" : ""
  }`;

  const badge = isTyping ? (
    <span className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-green-400 border-2 border-dark-800" />
  ) : null;

  const monogram = (
    <span aria-hidden="true" className="select-none">
      {PERSONA_INITIALS[persona]}
    </span>
  );

  return (
    <>
      <motion.button
        type="button"
        onClick={() => (hasVisuals ? setOpen(true) : undefined)}
        title={hasVisuals ? "Open Agent video" : "Software Steve"}
        aria-label={hasVisuals ? "Open Agent video" : "Software Steve"}
        className={`${baseClass} ${hasVisuals ? "" : "cursor-default"}`}
        whileHover={hasVisuals ? { scale: 1.05 } : undefined}
        whileTap={hasVisuals ? { scale: 0.95 } : undefined}
        transition={{ type: "spring", stiffness: 300 }}
      >
        {hasVisuals ? (
          <>
            <img
              src={still ? assets.still : assets.poster}
              alt=""
              aria-hidden="true"
              className="absolute inset-0 h-full w-full object-cover"
            />
            {!still && assets.video && (
              <video
                src={assets.video}
                poster={assets.poster}
                autoPlay
                loop
                muted
                playsInline
                preload="auto"
                className="absolute inset-0 h-full w-full object-cover"
              />
            )}
          </>
        ) : (
          monogram
        )}
        {badge}
      </motion.button>

      {open &&
        hasVisuals &&
        assets.video &&
        createPortal(
          <div
            className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4"
            role="dialog"
            aria-modal="true"
            aria-label="Agent video"
            onClick={() => setOpen(false)}
          >
            <video
              src={assets.video}
              poster={assets.poster}
              controls
              autoPlay
              loop
              playsInline
              className="max-h-[80vh] max-w-[90vw] rounded-xl shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            />
          </div>,
          document.body
        )}
    </>
  );
}
