import { Cpu, Terminal } from "lucide-react";

export type AgentType = "local" | "opencode";

interface AgentSwitchProps {
  value: AgentType;
  onChange: (type: AgentType) => void;
}

export default function AgentSwitch({ value, onChange }: AgentSwitchProps) {
  return (
    <div className="flex rounded-lg border border-dark-500 bg-dark-700 p-0.5">
      <button
        onClick={() => onChange("local")}
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
          value === "local"
            ? "bg-accent-blue/20 text-accent-blue border border-accent-blue/30"
            : "text-gray-500 hover:text-gray-300"
        }`}
      >
        <Cpu size={12} />
        Local
      </button>
      <button
        onClick={() => onChange("opencode")}
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
          value === "opencode"
            ? "bg-accent-purple/20 text-accent-purple border border-accent-purple/30"
            : "text-gray-500 hover:text-gray-300"
        }`}
      >
        <Terminal size={12} />
        OpenCode
      </button>
    </div>
  );
}
