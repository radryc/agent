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
  type:
    | "token"
    | "done"
    | "error"
    | "session"
    | "pong"
    | "exec_output"
    | "exec_done"
    | "exec_cwd"
    | "terminal_ready"
    | "terminal_output"
    | "terminal_exit"
    | "terminal_cwd"
    | "terminal_sync"
    | "terminal_error";
  data?: string;
  session_id?: string;
  exit_code?: number;
  cwd?: string;
  cols?: number;
  rows?: number;
  history?: string[];
}

export type AgentType = "local" | "opencode";

export type Mode = "agent" | "terminal";

export interface WSOutgoing {
  type:
    | "message"
    | "ping"
    | "exec"
    | "terminal_start"
    | "terminal_input"
    | "terminal_resize"
    | "terminal_signal"
    | "terminal_history";
  data?: string;
  session_id?: string;
  agent?: AgentType;
  cols?: number;
  rows?: number;
  signal?: string;
  command?: string;
}
