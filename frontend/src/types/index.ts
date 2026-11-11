export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  persona?: PersonaId | null;
  created_at: string;
}

export interface Session {
  id: string;
  title: string;
  active_persona?: PersonaId | null;
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

export type PersonaId = "otto" | "steve";
export type PersonaType = "auto" | PersonaId;

export type CrewAgentId =
  | "telemetry_hound"
  | "topology_auditor"
  | "software_steve"
  | "outage_otto";

export interface CrewStepEvent {
  type: "crew_step";
  agent_id: CrewAgentId;
  agent_name: string;
  task_name: string;
  status: "started" | "tool_call" | "tool_result" | "completed" | "failed";
  tool?: string;
  tool_args?: Record<string, unknown>;
  summary?: string;
  ts: string;
}

export interface ApprovalRequestEvent {
  type: "approval_required";
  id: string;
  tool:
    | "guardian_reconcile"
    | "guardian_deploy_partition"
    | "workspace_commit"
    | "workspace_push";
  args: Record<string, unknown>;
  rationale: string;
  diff?: string;
}

export interface WSIncoming {
  type:
    | "token"
    | "done"
    | "error"
    | "session"
    | "persona"
    | "pong"
    | "crew_step"
    | "approval_required"
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
  // persona event fields
  name?: string;
  role?: string;
  avatar?: string;
  accent?: string;
  engine?: string;
  engine_label?: string;
  reason?: string;
  // crew_step fields
  agent_id?: CrewAgentId;
  agent_name?: string;
  task_name?: string;
  status?: CrewStepEvent["status"];
  tool?: string;
  tool_args?: Record<string, unknown>;
  summary?: string;
  ts?: string;
  // approval_required fields
  id?: string;
  args?: Record<string, unknown>;
  rationale?: string;
  diff?: string;
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
    | "terminal_history"
    | "approval";
  data?: string;
  session_id?: string;
  agent?: AgentType;
  persona?: PersonaType;
  cols?: number;
  rows?: number;
  signal?: string;
  command?: string;
  // approval response
  id?: string;
  approved?: boolean;
}
