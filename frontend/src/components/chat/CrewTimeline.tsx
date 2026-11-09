import { useState } from "react";
import { ChevronDown, ChevronRight, Circle, Loader2, CheckCircle2, XCircle } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";
import type { CrewStepEvent } from "../../types";

const AGENT_ACCENT: Record<string, string> = {
  telemetry_hound: "text-accent-cyan",
  topology_auditor: "text-accent-blue",
  software_steve: "text-accent-purple",
  outage_otto: "text-amber-300",
};

function StatusIcon({ status }: { status: CrewStepEvent["status"] }) {
  if (status === "completed") return <CheckCircle2 size={12} className="text-emerald-400" />;
  if (status === "failed") return <XCircle size={12} className="text-red-400" />;
  if (status === "tool_call" || status === "tool_result")
    return <Loader2 size={12} className="animate-spin text-accent-cyan" />;
  return <Circle size={8} className="fill-current text-gray-500" />;
}

export default function CrewTimeline() {
  const { activeCrewSteps, clearCrewTrace } = useChatStore();
  const [open, setOpen] = useState(true);

  if (activeCrewSteps.length === 0) return null;

  const agents = Array.from(new Set(activeCrewSteps.map((step) => step.agent_id)));

  return (
    <div className="shrink-0 border-b border-dark-600 bg-dark-800/60">
      <div className="flex w-full items-center gap-2 px-4 py-2 text-xs text-gray-400">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-gray-200"
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <span className="font-semibold uppercase tracking-[0.12em] text-gray-500">
            Crew execution
          </span>
          <span className="rounded-full bg-dark-600 px-2 py-0.5 font-mono text-[10px] text-gray-300">
            {agents.length} agent{agents.length === 1 ? "" : "s"}
          </span>
          <span className="ml-auto text-[10px] text-gray-500">{activeCrewSteps.length} steps</span>
        </button>
        <button
          type="button"
          onClick={clearCrewTrace}
          className="rounded px-1.5 py-0.5 text-[10px] text-gray-500 hover:bg-dark-600 hover:text-gray-300"
        >
          clear
        </button>
      </div>

      {open && (
        <div className="max-h-44 overflow-y-auto px-4 pb-3">
          <div className="space-y-1">
            {activeCrewSteps.map((step, index) => (
              <div
                key={`${step.ts}-${index}`}
                className="flex items-start gap-2 rounded border border-dark-600/70 bg-dark-900/50 px-2 py-1.5 font-mono text-[10px]"
              >
                <StatusIcon status={step.status} />
                <span className={`shrink-0 font-semibold ${AGENT_ACCENT[step.agent_id] || "text-gray-300"}`}>
                  [{step.agent_name}]
                </span>
                <span className="shrink-0 text-gray-500">{step.task_name}</span>
                {step.tool && (
                  <span className="shrink-0 text-accent-cyan">
                    {step.tool}
                    {step.tool_args && Object.keys(step.tool_args).length > 0
                      ? `(${Object.keys(step.tool_args).join(", ")})`
                      : "()"}
                  </span>
                )}
                {step.summary && (
                  <span className="min-w-0 flex-1 truncate text-gray-500" title={step.summary}>
                    {step.summary}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
