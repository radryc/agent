import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import { api } from "../api/client";
import { Terminal, Cpu, ArrowRight, MessageSquare, Sparkles } from "lucide-react";

interface AgentInfo {
  id: string;
  name: string;
  description: string;
  available: boolean;
  default: boolean;
}

export default function LandingPage() {
  const navigate = useNavigate();
  const { sessions, createSession, activeAgent, setActiveAgent } = useChatStore();
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  useEffect(() => {
    api.agentStatus().then((res) => setAgents(res.agents)).catch(() => {});
  }, []);

  const handleNewSession = async () => {
    const id = await createSession();
    navigate(`/chat/${id}`);
  };

  const agentColor = (id: string) =>
    id === "local" ? "accent-blue" : "accent-purple";
  const agentIcon = (id: string) =>
    id === "local" ? Cpu : Terminal;

  return (
    <div className="h-full flex flex-col items-center justify-center bg-dark-900 px-6">
      <div className="max-w-2xl w-full text-center">
        {/* Logo / Icon */}
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-dark-700 border border-dark-600 mb-6">
          <Terminal size={28} className="text-accent-blue" />
        </div>

        {/* Title */}
        <h1 className="text-4xl font-bold text-gray-100 mb-3 tracking-tight">
          OutageOtto
        </h1>
        <p className="text-lg text-gray-500 max-w-md mx-auto mb-10">
          SRE supervision. Not customer support.
        </p>

        {/* Agent Selection */}
        <div className="mb-10">
          <p className="text-xs text-gray-600 uppercase tracking-wider mb-3">
            Select Agent
          </p>
          <div className="inline-flex rounded-xl border border-dark-500 bg-dark-800 p-1 gap-1">
            {agents.map((agent) => {
              const Icon = agentIcon(agent.id);
              const isActive = activeAgent === agent.id;
              const colorClass =
                agentColor(agent.id) === "accent-blue"
                  ? "bg-accent-blue/15 text-accent-blue ring-accent-blue/30"
                  : "bg-accent-purple/15 text-accent-purple ring-accent-purple/30";

              return (
                <button
                  key={agent.id}
                  onClick={() => agent.available && setActiveAgent(agent.id as "local" | "opencode")}
                  disabled={!agent.available}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    isActive
                      ? `${colorClass} ring-1`
                      : agent.available
                        ? "text-gray-500 hover:text-gray-300"
                        : "text-gray-700 cursor-not-allowed"
                  }`}
                >
                  <Icon size={15} />
                  <div className="text-left">
                    <div className="flex items-center gap-1.5">
                      {agent.name}
                      {!agent.available && (
                        <span className="text-[9px] bg-dark-600 px-1.5 py-0.5 rounded">
                          unavailable
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] opacity-60">{agent.description}</div>
                  </div>
                  {isActive && <Sparkles size={12} className="ml-1" />}
                </button>
              );
            })}
            {agents.length === 0 && (
              <>
                <button
                  onClick={() => setActiveAgent("local")}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    activeAgent === "local"
                      ? "bg-accent-blue/15 text-accent-blue ring-1 ring-accent-blue/30"
                      : "text-gray-500 hover:text-gray-300"
                  }`}
                >
                  <Cpu size={15} />
                  <div className="text-left">
                    <div>Local Agent</div>
                    <div className="text-[10px] opacity-60">Gemma 4 · llama.cpp</div>
                  </div>
                </button>
                <button
                  onClick={() => setActiveAgent("opencode")}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    activeAgent === "opencode"
                      ? "bg-accent-purple/15 text-accent-purple ring-1 ring-accent-purple/30"
                      : "text-gray-500 hover:text-gray-300"
                  }`}
                >
                  <Terminal size={15} />
                  <div className="text-left">
                    <div>OpenCode</div>
                    <div className="text-[10px] opacity-60">Code-first agent</div>
                  </div>
                </button>
              </>
            )}
          </div>
        </div>

        {/* CTA */}
        <button
          onClick={handleNewSession}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-accent-blue hover:bg-accent-blue/85 text-white font-medium transition-colors shadow-lg shadow-accent-blue/20"
        >
          <Terminal size={18} />
          New Terminal Session
          <ArrowRight size={16} />
        </button>

        <p className="text-xs text-gray-600 mt-4">
          Press Enter to send, Ctrl+C to cancel, Up/Down for history
        </p>

        {/* Recent Sessions */}
        {sessions.length > 0 && (
          <div className="mt-12 text-left">
            <h2 className="text-sm font-semibold text-gray-400 mb-3 flex items-center gap-2">
              <MessageSquare size={14} />
              Recent Sessions
            </h2>
            <div className="space-y-1">
              {sessions.slice(0, 5).map((s) => (
                <button
                  key={s.id}
                  onClick={() => navigate(`/chat/${s.id}`)}
                  className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-sm text-gray-400 hover:bg-dark-700 hover:text-gray-200 transition-colors text-left"
                >
                  <MessageSquare size={13} className="shrink-0" />
                  <span className="truncate">{s.title}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="mt-auto py-4 text-center">
        <p className="text-[11px] text-gray-700">
          Everything runs locally · Your code never leaves your machine
        </p>
      </div>
    </div>
  );
}
