import { useEffect, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useChatStore } from "../../stores/chatStore";
import AgentSwitch from "./AgentSwitch";
import AgentAvatar from "../chat/AgentAvatar";
import { Plus, Trash2, Settings, MessageSquare, X, Pencil } from "lucide-react";
import type { PersonaType } from "../../types";

interface SidebarProps {
  onClose: () => void;
}

const HEADERS: Record<PersonaType, { name: string; role: string; avatar: "otto" | "steve" }> = {
  auto: { name: "Otto + Steve", role: "SRE · Software Engineer", avatar: "otto" },
  otto: { name: "Outage Otto", role: "SRE Assistant", avatar: "otto" },
  steve: { name: "Software Steve", role: "Software Engineer", avatar: "steve" },
};

export default function Sidebar({ onClose }: SidebarProps) {
  const {
    sessions,
    activeSessionId,
    activePersona,
    init,
    cleanup,
    createSession,
    switchSession,
    deleteSession,
    renameSession,
    setActivePersona,
  } = useChatStore();
  const navigate = useNavigate();
  const location = useLocation();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  useEffect(() => {
    init();
    return () => cleanup();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAgentChange = (persona: PersonaType) => {
    setActivePersona(persona);
  };

  const handleNewChat = async () => {
    const id = await createSession();
    navigate(`/chat/${id}`);
    onClose();
  };

  const handleSwitch = async (id: string) => {
    await switchSession(id);
    navigate(`/chat/${id}`);
    onClose();
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    await deleteSession(id);
    if (activeSessionId === id) {
      navigate("/");
    }
  };

  return (
    <div className="flex flex-col h-full bg-dark-800 border-r border-dark-600">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 border-b border-dark-600">
        <AgentAvatar size="sm" still persona={HEADERS[activePersona].avatar} />
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold text-gray-100 truncate">
            {HEADERS[activePersona].name}
          </h1>
          <p className="text-xs text-gray-500">{HEADERS[activePersona].role}</p>
        </div>
        <button
          onClick={onClose}
          title="Hide sidebar"
          aria-label="Hide sidebar"
          className="p-1 rounded hover:bg-dark-600 transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Persona Switch */}
      <div className="px-3 pt-3 pb-2">
        <AgentSwitch value={activePersona} onChange={handleAgentChange} />
      </div>

      {/* New Chat */}
      <div className="px-3 pb-2">
        <button
          onClick={handleNewChat}
          className="flex items-center gap-2 w-full rounded-lg border border-dark-500 px-3 py-2.5 text-sm hover:bg-dark-700 transition-colors text-gray-300"
        >
          <Plus size={16} />
          New Terminal Session
        </button>
      </div>

      {/* Sessions */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1">
        {sessions.length === 0 && (
          <p className="text-xs text-gray-600 text-center py-4">
            No sessions yet
          </p>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            onClick={() => handleSwitch(s.id)}
            className={`group flex items-center gap-2 w-full rounded-lg px-3 py-2 text-sm text-left transition-colors cursor-pointer ${
              activeSessionId === s.id
                ? "bg-dark-600 text-gray-100"
                : "text-gray-400 hover:bg-dark-700 hover:text-gray-200"
            }`}
          >
            <MessageSquare size={14} className="shrink-0" />
            {editingId === s.id ? (
              <input
                className="flex-1 bg-dark-800 border border-accent-blue rounded px-1 py-0.5 text-xs text-gray-100 outline-none"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.stopPropagation();
                    renameSession(s.id, editTitle);
                    setEditingId(null);
                  } else if (e.key === "Escape") {
                    e.stopPropagation();
                    setEditingId(null);
                  }
                }}
                onBlur={() => {
                  if (editTitle.trim() && editTitle !== s.title) {
                    renameSession(s.id, editTitle);
                  }
                  setEditingId(null);
                }}
                onClick={(e) => e.stopPropagation()}
                autoFocus
              />
            ) : (
              <span
                className="flex-1 truncate"
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  setEditingId(s.id);
                  setEditTitle(s.title);
                }}
              >
                {s.title}
              </span>
            )}
            <span
              onClick={(e) => {
                e.stopPropagation();
                setEditingId(s.id);
                setEditTitle(s.title);
              }}
              className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-dark-500 transition-opacity"
              title="Rename"
            >
              <Pencil size={12} />
            </span>
            <span
              onClick={(e) => handleDelete(e, s.id)}
              className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-dark-500 transition-opacity"
            >
              <Trash2 size={12} />
            </span>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-dark-600">
        <button
          onClick={() => {
            navigate("/settings");
            onClose();
          }}
          className={`flex items-center gap-2 w-full rounded-lg px-3 py-2 text-sm transition-colors ${
            location.pathname === "/settings"
              ? "bg-dark-600 text-gray-100"
              : "text-gray-400 hover:bg-dark-700 hover:text-gray-200"
          }`}
        >
          <Settings size={14} />
          Settings
        </button>
      </div>
    </div>
  );
}
