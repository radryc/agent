import { motion } from "framer-motion";

interface AgentAvatarProps {
  size?: "sm" | "md" | "lg";
  isTyping?: boolean;
}

const sizes = {
  sm: "w-8 h-8 text-sm",
  md: "w-10 h-10 text-base",
  lg: "w-14 h-14 text-xl",
};

export default function AgentAvatar({ size = "md", isTyping = false }: AgentAvatarProps) {
  return (
    <motion.div
      className={`relative flex items-center justify-center rounded-full bg-gradient-to-br from-accent-blue to-accent-purple font-bold text-white ${sizes[size]} ${
        isTyping ? "animate-glow-pulse" : ""
      }`}
      whileHover={{ scale: 1.05 }}
      transition={{ type: "spring", stiffness: 300 }}
    >
      L
      {isTyping && (
        <span className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-green-400 border-2 border-dark-800" />
      )}
    </motion.div>
  );
}
