export interface AgentSettings {
  system_prompt: string;
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
      messages: Array<{ id: string; role: string; content: string; created_at: string }>;
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
      agents: Array<{
        id: string;
        name: string;
        description: string;
        available: boolean;
        default: boolean;
      }>;
      active: string;
    }>("/api/agent/status"),
};
