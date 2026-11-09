import type {
  NightwatchActivityResponse,
  NightwatchAssignment,
  NightwatchBrief,
  NightwatchIncidentDetail,
  NightwatchIncidentsResponse,
  NightwatchOverview,
} from "../types/nightwatch";

export interface AgentSettings {
  system_prompt: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
  llm_api_key_set?: boolean;
  llm_api_key_masked?: string;
  llm_grammar_enabled: boolean;
  llm_tool_grammar: string;
  llm_request_extra: string;
  llm_hybrid_enabled: boolean;
  llm_hybrid_after_rounds: number;
  llm_max_tool_rounds: number;
  llm_fallback_base_url: string;
  llm_fallback_model: string;
  llm_fallback_api_key: string;
  llm_fallback_api_key_set?: boolean;
  llm_fallback_api_key_masked?: string;
  llm_fallback_provider: string;
  llm_providers_configured?: boolean;
  gemini_api_key: string;
  gemini_api_key_set?: boolean;
  gemini_api_key_masked?: string;
  gemini_image_model: string;
  gemini_image_aspect_ratio: string;
  gemini_image_count: number;
  opencode_api_key: string;
  opencode_api_key_set?: boolean;
  opencode_api_key_masked?: string;
  opencode_server_url: string;
  opencode_model: string;
  opencode_provider_id: string;
  opencode_system_prompt: string;
  // Steve persona
  steve_engine: string;
  steve_llm_base_url: string;
  steve_llm_model: string;
  steve_llm_api_key: string;
  steve_llm_api_key_set?: boolean;
  steve_llm_api_key_masked?: string;
  steve_system_prompt: string;
  auto_switch_enabled: boolean;
  auto_push_enabled: boolean;
  agent_flow_enabled: boolean;
  // MonoFS workspace
  monofs_router: string;
  monofs_mount: string;
  monofs_overlay: string;
  // SSO / OIDC
  agent_oidc_issuer: string;
  agent_oidc_token_url: string;
  agent_oidc_authorization_url: string;
  agent_oidc_device_authorization_url: string;
  agent_oidc_redirect_url: string;
  agent_oidc_client_id: string;
  agent_oidc_client_secret: string;
  agent_oidc_client_secret_set?: boolean;
  agent_oidc_client_secret_masked?: string;
  agent_oidc_audience: string;
  agent_oidc_scope: string;
  agent_oidc_token: string;
  agent_oidc_token_set?: boolean;
  agent_oidc_token_masked?: string;
  agent_oidc_public_base_url: string;
  workspace?: WorkspaceStatus;
}

export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
  authenticated_as_user?: boolean;
  source: string;
  subject: string;
  expires_at: number | null;
  error: string;
  device_flow_available: boolean;
  browser_login_available?: boolean;
  token_url: string;
  issuer: string;
  public_base_url?: string;
}

export interface DeviceLoginStart {
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string;
  expires_in: number;
  interval: number;
}

export interface DeviceLoginPoll {
  status: "authorization_pending" | "slow_down" | "authorized" | "expired" | "denied";
  interval?: number;
  subject?: string;
  expires_at?: number;
}

export interface WorkspaceStatus {
  mount: string;
  overlay: string;
  router: string;
  mounted: boolean;
  socket: boolean;
  writable: boolean;
}

export interface PersonaStatus {
  id: "otto" | "steve";
  name: string;
  role: string;
  summary: string;
  avatar: string;
  accent: string;
  engine: {
    id: string;
    label: string;
    available: boolean;
    detail: string;
  };
}

export interface ModelInfo {
  id: string;
  name: string;
}

export interface OpenCodeModelInfo {
  provider_id: string;
  model_id: string;
  display_name: string;
}

export interface ProviderInfo {
  id: string;
  name: string;
}

export interface ModelPreset {
  id: string;
  label: string;
  model: string;
  repo: string;
  file: string;
  license: string;
}

export interface AgentModelsResponse {
  local: {
    provider: string;
    base_url: string;
    current_model: string;
    models: ModelInfo[];
    presets?: ModelPreset[];
    grammar_enabled?: boolean;
  };
  profiles?: Array<{
    id: string;
    available: boolean;
    label: string;
    base_url?: string;
    model?: string;
  }>;
  hybrid?: {
    enabled: boolean;
    fallback_ready: boolean;
    after_rounds: number;
  };
  opencode: {
    provider: string;
    server_url: string;
    current_model: string;
    models: OpenCodeModelInfo[];
    providers: ProviderInfo[];
  };
  known_providers: ProviderInfo[];
}

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  health: () => request<{ status: string; llm: string; version: string }>("/api/health"),

  agentInfo: () => request<{ name: string; system_prompt: string; model: string }>("/api/agent"),

  listSessions: () => request<Array<{ id: string; title: string; created_at: string; updated_at: string }>>("/api/sessions"),

  getSession: (id: string) =>
    request<{
      id: string;
      title: string;
      active_persona?: string | null;
      messages: Array<{
        id: string;
        role: string;
        content: string;
        persona?: string | null;
        created_at: string;
      }>;
    }>(`/api/sessions/${id}`),

  createSession: () => request<{ id: string; title: string }>("/api/sessions", { method: "POST" }),

  deleteSession: (id: string) => request<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),

  updateSession: (id: string, title: string) =>
    request<{ id: string; title: string }>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  getSettings: () => request<AgentSettings>("/api/settings"),

  saveSettings: (payload: Partial<AgentSettings>) =>
    request<AgentSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  // SSO / OIDC
  authStatus: () => request<AuthStatus>("/api/auth/status"),

  startDeviceLogin: () =>
    request<DeviceLoginStart>("/api/auth/device/start", { method: "POST" }),

  pollDeviceLogin: (device_code?: string) =>
    request<DeviceLoginPoll>("/api/auth/device/poll", {
      method: "POST",
      body: JSON.stringify({ device_code }),
    }),

  logoutSso: () =>
    request<{ status: string }>("/api/auth/logout", { method: "POST" }),

  listModels: () => request<AgentModelsResponse>("/api/agent/models"),

  // Guardian proxy
  guardianOverview: () =>
    request<{
      generatedAt: string;
      summary: {
        partitions: number;
        intents: number;
        assets: number;
        healthyAssets: number;
        attentionAssets: number;
        failingAssets: number;
        healthyIntents: number;
        driftedIntents: number;
        failedIntents: number;
        servicesHealthy: number;
        servicesAttention: number;
      };
      partitions: Array<{
        name: string;
        status: string;
        displayStatus: string;
        health: string;
        intentCount: number;
        assetCount: number;
        healthyAssets: number;
        attentionAssets: number;
        failingAssets: number;
        healthyIntents: number;
        driftedIntents: number;
        failedIntents: number;
        lastReconciledAt: string;
      }>;
    }>("/api/guardian/overview"),

  // Agent management
  agentStatus: () =>
    request<{
      personas: PersonaStatus[];
      workspace: WorkspaceStatus;
      auto_switch_enabled: boolean;
      auto_push_enabled: boolean;
      agent_flow_enabled: boolean;
      agents: Array<{
        id: string;
        name: string;
        description: string;
        available: boolean;
        default: boolean;
      }>;
      active: string;
    }>("/api/agent/status"),

  // Nightwatch (shadow-mode SRE observer)
  nightwatchOverview: () =>
    request<NightwatchOverview>("/api/nightwatch/overview"),

  nightwatchBrief: (since?: string) =>
    request<NightwatchBrief>(
      `/api/nightwatch/brief${since ? `?since=${encodeURIComponent(since)}` : ""}`
    ),

  nightwatchAssignments: () =>
    request<{ available: boolean; assignments: NightwatchAssignment[] }>(
      "/api/nightwatch/assignments"
    ),

  nightwatchIncidents: (params?: { status?: string; partition?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.status) search.set("status", params.status);
    if (params?.partition) search.set("partition", params.partition);
    if (params?.limit) search.set("limit", String(params.limit));
    const query = search.toString();
    return request<NightwatchIncidentsResponse>(
      `/api/nightwatch/incidents${query ? `?${query}` : ""}`
    );
  },

  nightwatchIncident: (id: string) =>
    request<NightwatchIncidentDetail>(`/api/nightwatch/incidents/${encodeURIComponent(id)}`),

  nightwatchActivity: (params?: { since?: string; partition?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.since) search.set("since", params.since);
    if (params?.partition) search.set("partition", params.partition);
    if (params?.limit) search.set("limit", String(params.limit));
    const query = search.toString();
    return request<NightwatchActivityResponse>(
      `/api/nightwatch/activity${query ? `?${query}` : ""}`
    );
  },

  createSteveProposal: (body: {
    incident_id: string;
    incident_title: string;
    partition: string;
    severity: string;
    summary: string;
    alertname: string;
    labels: Record<string, string>;
    improvement: {
      id: string;
      title: string;
      category: string;
      rationale: string;
      proposed_change: string;
      target: string;
      confidence: number;
    };
  }) =>
    request<{ session_id: string; status: string }>("/api/agent/proposal", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
