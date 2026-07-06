import { create } from "zustand";
import type { Message, Session, WSIncoming, AgentType, Mode } from "../types";
import { ChatWebSocket } from "../api/websocket";
import { api } from "../api/client";

interface ChatState {
  sessions: Session[];
  activeSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  streamContent: string;
  ws: ChatWebSocket | null;
  activeAgent: AgentType;
  mode: Mode;
  cwd: string;
  execOutput: string;
  terminalConnected: boolean;
  terminalHistory: string[];

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
  setMode: (mode: Mode) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  isStreaming: false,
  streamContent: "",
  ws: null,
  activeAgent: "local",
  mode: "agent",
  cwd: "~",
  execOutput: "",
  terminalConnected: false,
  terminalHistory: [],

  init: () => {
    const handler = (msg: WSIncoming) => {
      const state = get();

      if (msg.type === "session" && msg.session_id) {
        if (!state.activeSessionId) {
          set({ activeSessionId: msg.session_id });
        }
        get().loadSessions();
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
            created_at: new Date().toISOString(),
          };
          set((s) => ({
            messages: [...s.messages, assistantMsg],
            isStreaming: false,
            streamContent: "",
          }));
        } else {
          set({ isStreaming: false, streamContent: "" });
        }
      }

      if (msg.type === "error") {
        set({ isStreaming: false, streamContent: "", execOutput: "" });
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
        set({ isStreaming: false, streamContent: "", terminalConnected: false });
      },
      onError: () => {
        set({ isStreaming: false, streamContent: "", terminalConnected: false });
      },
      onOpen: () => {
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
    set({ ws: null });
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
    set({ activeSessionId: session.id, messages: [] });
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
        created_at: m.created_at,
      }));
      set({ activeSessionId: id, messages, isStreaming: false, streamContent: "" });
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
      set({ activeSessionId: null, messages: [] });
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
        agent: state.activeAgent,
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

  setMode: (mode: Mode) => {
    set({ mode });
    if (mode === "terminal") {
      get().startTerminal();
    }
  },
}));
