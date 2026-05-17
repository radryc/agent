import { create } from "zustand";
import type { Message, Session, WSIncoming } from "../types";
import { ChatWebSocket } from "../api/websocket";
import { api } from "../api/client";

interface ChatState {
  sessions: Session[];
  activeSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  streamContent: string;
  ws: ChatWebSocket | null;

  // Actions
  init: () => void;
  cleanup: () => void;
  loadSessions: () => Promise<void>;
  createSession: () => Promise<string>;
  switchSession: (id: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  sendMessage: (text: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  isStreaming: false,
  streamContent: "",
  ws: null,

  init: () => {
    const handler = (msg: WSIncoming) => {
      const state = get();

      if (msg.type === "session" && msg.session_id) {
        if (!state.activeSessionId) {
          set({ activeSessionId: msg.session_id });
        }
        // Reload sessions to pick up the new one
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
        set({ isStreaming: false, streamContent: "" });
      }
    };

    const ws = new ChatWebSocket(handler);
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

  sendMessage: (text: string) => {
    const state = get();
    if (state.isStreaming || !text.trim()) return;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      session_id: state.activeSessionId || "",
      role: "user",
      content: text.trim(),
      created_at: new Date().toISOString(),
    };

    set((s) => ({
      messages: [...s.messages, userMsg],
      isStreaming: true,
      streamContent: "",
    }));

    state.ws?.send({
      type: "message",
      data: text.trim(),
      session_id: state.activeSessionId || undefined,
    });
  },
}));
