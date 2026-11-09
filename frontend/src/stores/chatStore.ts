import { create } from "zustand";
import type { Message, Session, WSIncoming, AgentType, PersonaType, PersonaId, Mode, CrewStepEvent, ApprovalRequestEvent } from "../types";
import { ChatWebSocket } from "../api/websocket";
import { api } from "../api/client";

interface ChatState {
  sessions: Session[];
  activeSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  streamContent: string;
  /** Last stream failure, surfaced in the chat instead of silently dropping the turn. */
  streamError: string | null;
  ws: ChatWebSocket | null;
  /** True once the agent websocket is open; used by the embed view auto-prompt. */
  agentConnected: boolean;
  activeAgent: AgentType;
  activePersona: PersonaType;
  /** Persona that answered the current/streamed turn (from the WS persona event). */
  streamPersona: PersonaId | null;
  streamEngine: string | null;
  mode: Mode;
  cwd: string;
  execOutput: string;
  terminalConnected: boolean;
  terminalHistory: string[];
  activeCrewSteps: CrewStepEvent[];
  pendingApprovals: ApprovalRequestEvent[];

  // Actions
  init: () => void;
  cleanup: () => void;
  loadSessions: () => Promise<void>;
  createSession: () => Promise<string>;
  switchSession: (id: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  sendMessage: (text: string) => boolean;
  startTerminal: () => boolean;
  sendTerminalInput: (data: string) => boolean;
  sendTerminalResize: (cols: number, rows: number) => void;
  sendTerminalSignal: (signal?: string) => void;
  addTerminalHistory: (command: string) => void;
  setActiveAgent: (agent: AgentType) => void;
  setActivePersona: (persona: PersonaType) => void;
  setMode: (mode: Mode) => void;
  approveMutation: (id: string, approved: boolean) => boolean;
  clearCrewTrace: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  isStreaming: false,
  streamContent: "",
  streamError: null,
  ws: null,
  agentConnected: false,
  activeAgent: "local",
  activePersona: "auto",
  streamPersona: null,
  streamEngine: null,
  mode: "agent",
  cwd: "~",
  execOutput: "",
  terminalConnected: false,
  terminalHistory: [],
  activeCrewSteps: [],
  pendingApprovals: [],

  init: () => {
    const handler = (msg: WSIncoming) => {
      const state = get();

      if (msg.type === "session" && msg.session_id) {
        if (!state.activeSessionId) {
          set({ activeSessionId: msg.session_id });
        }
        get().loadSessions();
      }

      if (msg.type === "persona") {
        set({
          streamPersona: (msg.data as PersonaId) || null,
          streamEngine: msg.engine_label || msg.engine || null,
        });
      }

      if (msg.type === "crew_step" && msg.agent_id && msg.task_name && msg.status) {
        const step: CrewStepEvent = {
          type: "crew_step",
          agent_id: msg.agent_id,
          agent_name: msg.agent_name || msg.agent_id,
          task_name: msg.task_name,
          status: msg.status,
          tool: msg.tool,
          tool_args: msg.tool_args,
          summary: msg.summary,
          ts: msg.ts || new Date().toISOString(),
        };
        set((s) => ({
          activeCrewSteps: [...s.activeCrewSteps, step].slice(-80),
        }));
      }

      if (msg.type === "approval_required" && msg.id && msg.tool) {
        const approval: ApprovalRequestEvent = {
          type: "approval_required",
          id: msg.id,
          tool: msg.tool as ApprovalRequestEvent["tool"],
          args: msg.args || {},
          rationale: msg.rationale || "",
          diff: msg.diff,
        };
        set((s) => ({
          pendingApprovals: [
            ...s.pendingApprovals.filter((item) => item.id !== approval.id),
            approval,
          ],
        }));
      }

      if (msg.type === "token" && msg.data) {
        set((s) => ({ streamContent: s.streamContent + msg.data }));
      }

      if (msg.type === "done") {
        const content = get().streamContent;
        if (content) {
          const assistantMsg: Message = {
            id: crypto.randomUUID(),
            session_id: msg.session_id || state.activeSessionId || "",
            role: "assistant",
            content,
            persona: get().streamPersona,
            created_at: new Date().toISOString(),
          };
          set((s) => ({
            messages: [...s.messages, assistantMsg],
            isStreaming: false,
            streamContent: "",
            streamError: null,
          }));
        } else {
          set({ isStreaming: false, streamContent: "", streamError: null });
        }
      }

      if (msg.type === "error") {
        const detail = msg.data || "The agent request failed.";
        console.error("[agent] stream error:", detail);
        const partial = get().streamContent;
        set((s) => ({
          messages: partial
            ? [
                ...s.messages,
                {
                  id: crypto.randomUUID(),
                  session_id: msg.session_id || s.activeSessionId || "",
                  role: "assistant" as const,
                  content: partial,
                  persona: s.streamPersona,
                  created_at: new Date().toISOString(),
                },
              ]
            : s.messages,
          isStreaming: false,
          streamContent: "",
          streamError: detail,
          execOutput: "",
          streamPersona: null,
        }));
      }

      if (msg.type === "exec_output" && msg.data) {
        set((s) => ({ execOutput: s.execOutput + msg.data }));
      }

      if (msg.type === "exec_done") {
        set({ isStreaming: false, execOutput: "" });
      }

      if (msg.type === "exec_cwd" && msg.cwd) {
        set({ cwd: msg.cwd });
      }

      if (msg.type === "terminal_ready") {
        set({ terminalConnected: true });
      }

      if (msg.type === "terminal_output" && msg.data) {
        set((s) => ({ execOutput: s.execOutput + msg.data }));
      }

      if (msg.type === "terminal_exit") {
        set({ terminalConnected: false, isStreaming: false });
      }

      if (msg.type === "terminal_cwd" && msg.cwd) {
        set({ cwd: msg.cwd });
      }

      if (msg.type === "terminal_sync") {
        set({
          terminalHistory: msg.history || [],
          cwd: msg.cwd || get().cwd,
          terminalConnected: true,
        });
      }

      if (msg.type === "terminal_error") {
        set({ terminalConnected: false, isStreaming: false });
      }
    };

    const ws = new ChatWebSocket(handler, {
      onClose: () => {
        set({ agentConnected: false });
        const state = get();
        if (state.isStreaming) {
          const partial = state.streamContent;
          set((s) => ({
            messages: partial
              ? [
                  ...s.messages,
                  {
                    id: crypto.randomUUID(),
                    session_id: s.activeSessionId || "",
                    role: "assistant" as const,
                    content: partial,
                    persona: s.streamPersona,
                    created_at: new Date().toISOString(),
                  },
                ]
              : s.messages,
            isStreaming: false,
            streamContent: "",
            streamError: "Connection to the agent was interrupted. Reconnecting…",
            terminalConnected: false,
          }));
          return;
        }
        set({ isStreaming: false, streamContent: "", terminalConnected: false });
      },
      onError: () => {
        set((s) => ({
          isStreaming: false,
          streamContent: "",
          streamError: s.isStreaming
            ? "Could not reach the agent backend."
            : s.streamError,
          terminalConnected: false,
          agentConnected: false,
        }));
      },
      onOpen: () => {
        set({ agentConnected: true });
        if (get().mode === "terminal") {
          get().startTerminal();
        }
      },
    });
    ws.connect();
    set({ ws });

    get().loadSessions();
  },

  cleanup: () => {
    get().ws?.disconnect();
    set({ ws: null, agentConnected: false });
  },

  loadSessions: async () => {
    try {
      const sessions = await api.listSessions();
      set({ sessions });
    } catch {
      // ignore
    }
  },

  createSession: async () => {
    const session = await api.createSession();
    await get().loadSessions();
    set({ activeSessionId: session.id, messages: [], streamError: null });
    return session.id;
  },

  switchSession: async (id: string) => {
    try {
      const session = await api.getSession(id);
      const messages: Message[] = (session.messages || []).map((m) => ({
        id: m.id,
        session_id: id,
        role: m.role as "user" | "assistant",
        content: m.content,
        persona: (m.persona as PersonaId) ?? null,
        created_at: m.created_at,
      }));
      set({
        activeSessionId: id,
        messages,
        isStreaming: false,
        streamContent: "",
        streamError: null,
        streamPersona: null,
        streamEngine: null,
      });
      if (get().mode === "terminal") {
        get().startTerminal();
      }
    } catch {
      // ignore
    }
  },

  deleteSession: async (id: string) => {
    await api.deleteSession(id);
    const state = get();
    if (state.activeSessionId === id) {
      set({ activeSessionId: null, messages: [], streamError: null });
    }
    await get().loadSessions();
  },

  renameSession: async (id: string, title: string) => {
    await api.updateSession(id, title.trim() || "New Chat");
    await get().loadSessions();
  },

  sendMessage: (text: string) => {
    const state = get();
    if (state.isStreaming || !text.trim()) return false;

    const payloadText = text.trim();

    if (state.mode === "terminal") {
      const sent =
        state.ws?.send({
          type: "exec",
          data: payloadText,
          session_id: state.activeSessionId || undefined,
        }) ?? false;

      if (!sent) return false;

      set({ isStreaming: true, execOutput: "" });
      return true;
    }

    const sent =
      state.ws?.send({
        type: "message",
        data: payloadText,
        session_id: state.activeSessionId || undefined,
        persona: state.activePersona,
      }) ?? false;

    if (!sent) {
      return false;
    }

    const userMsg: Message = {
      id: crypto.randomUUID(),
      session_id: state.activeSessionId || "",
      role: "user",
      content: payloadText,
      created_at: new Date().toISOString(),
    };

    set((s) => ({
      messages: [...s.messages, userMsg],
      isStreaming: true,
      streamContent: "",
      streamError: null,
      streamPersona: null,
      activeCrewSteps: [],
    }));

    return true;
  },

  startTerminal: () => {
    const state = get();
    const sent =
      state.ws?.send({
        type: "terminal_start",
        session_id: state.activeSessionId || undefined,
      }) ?? false;
    if (sent) {
      set({ terminalConnected: true, execOutput: "" });
    }
    return sent;
  },

  sendTerminalInput: (data: string) => {
    const state = get();
    if (!state.terminalConnected) {
      const started = get().startTerminal();
      if (!started) return false;
    }

    return (
      state.ws?.send({
        type: "terminal_input",
        data,
      }) ?? false
    );
  },

  sendTerminalResize: (cols: number, rows: number) => {
    get().ws?.send({
      type: "terminal_resize",
      cols,
      rows,
    });
  },

  sendTerminalSignal: (signal = "SIGINT") => {
    get().ws?.send({
      type: "terminal_signal",
      signal,
    });
  },

  addTerminalHistory: (command: string) => {
    const trimmed = command.trim();
    if (!trimmed) return;

    set((s) => ({ terminalHistory: [...s.terminalHistory, trimmed] }));
    get().ws?.send({
      type: "terminal_history",
      command: trimmed,
    });
  },

  setActiveAgent: (agent: AgentType) => {
    set({ activeAgent: agent });
  },

  setActivePersona: (persona: PersonaType) => {
    set({ activePersona: persona });
  },

  setMode: (mode: Mode) => {
    set({ mode });
    if (mode === "terminal") {
      get().startTerminal();
    }
  },

  approveMutation: (id: string, approved: boolean) => {
    const state = get();
    const approval = state.pendingApprovals.find((item) => item.id === id);
    if (!approval) return false;
    const sent =
      state.ws?.send({
        type: "approval",
        id,
        approved,
        session_id: state.activeSessionId || undefined,
      }) ?? false;
    if (!sent) return false;
    set((s) => ({
      pendingApprovals: s.pendingApprovals.filter((item) => item.id !== id),
      isStreaming: true,
      streamContent: "",
      streamError: null,
    }));
    return true;
  },

  clearCrewTrace: () => {
    set({ activeCrewSteps: [] });
  },
}));
