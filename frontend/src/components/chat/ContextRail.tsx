import { Link } from "react-router-dom";
import {
  FolderOpen, MessageSquare, Settings, Terminal, UserRound,
} from "lucide-react";
import { useChatStore } from "../../stores/chatStore";

const PERSONA_LABELS = {
  auto: "Auto — Otto + Steve",
  otto: "Outage Otto",
  steve: "Software Steve",
} as const;

export default function ContextRail() {
  const {
    activeSessionId,
    sessions,
    activePersona,
    mode,
    cwd,
    terminalConnected,
    terminalHistory,
  } = useChatStore();
  const session = sessions.find((item) => item.id === activeSessionId);

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-l border-dark-600 bg-dark-800/70 xl:flex">
      <div className="border-b border-dark-600 px-4 py-4">
        <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500">
          Session context
        </div>
        <h2 className="mt-2 flex items-center gap-2 text-sm font-semibold text-gray-200">
          <MessageSquare size={14} className="text-accent-blue" />
          <span className="truncate">{session?.title || "New chat"}</span>
        </h2>
        <p className="mt-1 font-mono text-[10px] text-gray-500">
          {activeSessionId?.slice(0, 8) || "not started"}
        </p>
      </div>

      <div className="space-y-4 px-4 py-4">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500">
            Persona
          </div>
          <div className="flex items-center gap-2 text-sm text-gray-300">
            <UserRound size={14} className="text-accent-purple" />
            {PERSONA_LABELS[activePersona]}
          </div>
        </div>

        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500">
            Mode
          </div>
          <div className="flex items-center gap-2 text-sm text-gray-300">
            <Terminal size={14} className={mode === "terminal" ? "text-emerald-400" : "text-accent-purple"} />
            {mode === "terminal" ? "Terminal" : "Agent"}
            {mode === "terminal" && (
              <span className={`ml-auto text-[10px] ${terminalConnected ? "text-emerald-400" : "text-amber-400"}`}>
                {terminalConnected ? "connected" : "connecting"}
              </span>
            )}
          </div>
        </div>

        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500">
            Working directory
          </div>
          <div className="flex items-center gap-2 truncate font-mono text-xs text-gray-400">
            <FolderOpen size={14} className="shrink-0 text-accent-cyan" />
            <span className="truncate">{cwd || "~"}</span>
          </div>
        </div>

        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500">
            Recent commands
          </div>
          {terminalHistory.length === 0 ? (
            <p className="text-xs text-gray-500">No commands in this session yet.</p>
          ) : (
            <ul className="space-y-1">
              {terminalHistory.slice(-5).reverse().map((command, index) => (
                <li key={`${command}-${index}`} className="truncate rounded border border-dark-600 bg-dark-900/60 px-2 py-1 font-mono text-[10px] text-gray-400">
                  {command}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-auto border-t border-dark-600 p-3">
        <Link to="/settings" className="s-btn s-btn-ghost w-full">
          <Settings size={14} />
          Settings
        </Link>
      </div>
    </aside>
  );
}
