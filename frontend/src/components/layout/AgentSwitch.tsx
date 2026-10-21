import { Sparkles, Cpu, Code2 } from "lucide-react";
import type { PersonaType } from "../../types";

interface AgentSwitchProps {
  value: PersonaType;
  onChange: (persona: PersonaType) => void;
}

const OPTIONS: Array<{
  id: PersonaType;
  label: string;
  title: string;
  icon: typeof Sparkles;
  activeClass: string;
}> = [
  {
    id: "auto",
    label: "Auto",
    title: "Route each message to Otto (SRE) or Steve (coding) based on context",
    icon: Sparkles,
    activeClass: "bg-accent-blue/20 text-accent-blue border border-accent-blue/30",
  },
  {
    id: "otto",
    label: "Otto",
    title: "Outage Otto — SRE assistant",
    icon: Cpu,
    activeClass: "bg-accent-blue/20 text-accent-blue border border-accent-blue/30",
  },
  {
    id: "steve",
    label: "Steve",
    title: "Software Steve — software engineer",
    icon: Code2,
    activeClass: "bg-accent-purple/20 text-accent-purple border border-accent-purple/30",
  },
];

export default function AgentSwitch({ value, onChange }: AgentSwitchProps) {
  return (
    <div className="flex rounded-lg border border-dark-500 bg-dark-700 p-0.5">
      {OPTIONS.map((option) => {
        const Icon = option.icon;
        const isActive = value === option.id;
        return (
          <button
            key={option.id}
            onClick={() => onChange(option.id)}
            title={option.title}
            className={`flex flex-1 items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
              isActive ? option.activeClass : "text-gray-500 hover:text-gray-300"
            }`}
          >
            <Icon size={12} />
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
