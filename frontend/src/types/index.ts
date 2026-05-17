export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages?: Message[];
}

export interface AgentInfo {
  name: string;
  system_prompt: string;
  model: string;
}

export interface HealthStatus {
  status: string;
  llm: string;
  version: string;
}

export interface WSIncoming {
  type: "token" | "done" | "error" | "session" | "pong";
  data?: string;
  session_id?: string;
}

export interface WSOutgoing {
  type: "message" | "ping";
  data?: string;
  session_id?: string;
}
