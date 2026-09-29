import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";

interface AgentAvatarProps {
  size?: "sm" | "md" | "lg";
  isTyping?: boolean;
  /** Render a static still frame instead of the (muted) video. */
  still?: boolean;
}

const sizes = {
  sm: "w-8 h-8 text-sm",
  md: "w-10 h-10 text-base",
  lg: "w-14 h-14 text-xl",
};

const VIDEO_SRC = "/otto.mp4";
const POSTER_SRC = "/otto-poster.jpg";
const STILL_SRC = "/otto-avatar.jpg";

export default function AgentAvatar({ size = "md", isTyping = false, still = false }: AgentAvatarProps) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const baseClass = `relative flex items-center justify-center overflow-hidden rounded-full bg-gradient-to-br from-accent-blue to-accent-purple font-bold text-white ${sizes[size]} ${
    isTyping ? "animate-glow-pulse" : ""
  }`;

  const badge = isTyping ? (
    <span className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-green-400 border-2 border-dark-800" />
  ) : null;

  return (
    <>
      <motion.button
        type="button"
        onClick={() => setOpen(true)}
        title="Open Agent video"
        aria-label="Open Agent video"
        className={baseClass}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        transition={{ type: "spring", stiffness: 300 }}
      >
        <img
          src={still ? STILL_SRC : POSTER_SRC}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 h-full w-full object-cover"
        />
        {!still && (
          <video
            src={VIDEO_SRC}
            poster={POSTER_SRC}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            className="absolute inset-0 h-full w-full object-cover"
          />
        )}
        {badge}
      </motion.button>

      {open &&
        createPortal(
          <div
            className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4"
            role="dialog"
            aria-modal="true"
            aria-label="Agent video"
            onClick={() => setOpen(false)}
          >
            <video
              src={VIDEO_SRC}
              poster={POSTER_SRC}
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
