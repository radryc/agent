import { useEffect, useState } from "react";
import { api, AgentSettings, AgentModelsResponse } from "../api/client";
import AgentAvatar from "../components/chat/AgentAvatar";
import { Activity, Cpu, Server, Save, Bot, KeyRound, Terminal, RefreshCw } from "lucide-react";

export default function SettingsPage() {
  const [health, setHealth] = useState<{ status: string; llm: string; version: string } | null>(null);
  const [agentModel, setAgentModel] = useState("gemma-4-E4B-it-GGUF (Q4_K_M)");
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [models, setModels] = useState<AgentModelsResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);

  function loadData() {
    api.health().then(setHealth).catch(() => {});
    api.agentInfo().then((a) => setAgentModel(a.model)).catch(() => {});
    api.getSettings().then(setSettings).catch(() => {});
    refreshModels();
  }

  useEffect(() => { loadData(); }, []);

  function refreshModels() {
    setLoadingModels(true);
    api.listModels().then(setModels).catch(() => {}).finally(() => setLoadingModels(false));
  }

  function update<K extends keyof AgentSettings>(key: K, value: AgentSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function save() {
    if (!settings) return;
    setSaving(true);
    try {
      const payload: Partial<AgentSettings> = { ...settings };
      if (!payload.gemini_api_key?.trim()) {
        delete payload.gemini_api_key;
      }
      if (!payload.opencode_api_key?.trim()) {
        delete payload.opencode_api_key;
      }
      const updated = await api.saveSettings(payload);
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-8">
      <div className="max-w-2xl mx-auto space-y-8">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-100">Settings</h1>
          <button
            onClick={save}
            disabled={saving || !settings}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-blue hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
          >
            <Save size={14} />
            {saving ? "Saving…" : saved ? "Saved!" : "Save Settings"}
          </button>
        </div>

        {/* Agent Identity */}
        <section className="glass rounded-xl p-6 space-y-4">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
            <Bot size={16} className="text-accent-blue" />
            Agent
          </h2>
          <div className="flex items-center gap-4">
            <AgentAvatar size="lg" />
            <div>
              <p className="font-semibold">OutageOtto</p>
              <div className="flex items-center gap-2 mt-0.5">
                <Cpu size={12} className="text-accent-purple" />
                <span className="text-xs text-gray-500">{agentModel}</span>
              </div>
            </div>
          </div>

          {/* Local LLM Model */}
          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              Local LLM Model
            </label>
            <select
              value={settings?.llm_model ?? "gemma-4-E4B-it"}
              onChange={(e) => update("llm_model", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
            >
              <option value="gemma-4-E4B-it">gemma-4-E4B-it (default)</option>
              {models?.local.models
                .filter((m) => m.id !== "gemma-4-E4B-it")
                .map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
            </select>
            <p className="text-xs text-gray-600">
              Model name passed to llama.cpp / Ollama / vLLM. The backend queries the LLM server for available models.
              Current: <code className="text-accent-blue">{settings?.llm_model ?? "gemma-4-E4B-it"}</code>
            </p>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              System Prompt
            </label>
            <textarea
              rows={8}
              value={settings?.system_prompt ?? ""}
              onChange={(e) => update("system_prompt", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 p-3 text-sm text-gray-200 focus:outline-none focus:border-accent-blue resize-y font-mono leading-relaxed"
              placeholder="Loading…"
            />
          </div>
        </section>

        {/* Gemini Image Generation */}
        <section className="glass rounded-xl p-6 space-y-5">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
            <KeyRound size={16} className="text-accent-blue" />
            Gemini Image Generation (Imagen)
          </h2>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              Gemini API Key
              <span className="ml-1 normal-case text-gray-600">(stored on backend, optional per-tool override)</span>
            </label>
            <input
              type="password"
              placeholder={
                settings?.gemini_api_key_set
                  ? `Configured: ${settings.gemini_api_key_masked ?? "(hidden)"}`
                  : "Paste your Gemini API key"
              }
              value={settings?.gemini_api_key ?? ""}
              onChange={(e) => update("gemini_api_key", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
            />
            <p className="text-xs text-gray-600">
              Leave empty to keep current key. Enter a new value and click Save to update.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Imagen Model</label>
              <input
                type="text"
                value={settings?.gemini_image_model ?? "imagen-3.0-generate-002"}
                onChange={(e) => update("gemini_image_model", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Aspect Ratio</label>
              <select
                value={settings?.gemini_image_aspect_ratio ?? "16:9"}
                onChange={(e) => update("gemini_image_aspect_ratio", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              >
                {[
                  "1:1",
                  "3:4",
                  "4:3",
                  "9:16",
                  "16:9",
                ].map((ratio) => (
                  <option key={ratio} value={ratio}>{ratio}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-2 max-w-xs">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">Default Image Count</label>
            <input
              type="number"
              min={1}
              max={4}
              value={settings?.gemini_image_count ?? 1}
              onChange={(e) => update("gemini_image_count", Math.max(1, Math.min(4, parseInt(e.target.value || "1"))))}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
            />
          </div>
        </section>

        {/* OpenCode Agent */}
        <section className="glass rounded-xl p-6 space-y-4">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
            <Terminal size={16} className="text-accent-purple" />
            OpenCode Agent
          </h2>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              OpenCode API Key
              <span className="ml-1 normal-case text-gray-600">(required for Go / remote providers)</span>
            </label>
            <input
              type="password"
              placeholder={
                settings?.opencode_api_key_set
                  ? `Configured: ${settings.opencode_api_key_masked ?? "(hidden)"}`
                  : "Paste your OpenCode API key"
              }
              value={settings?.opencode_api_key ?? ""}
              onChange={(e) => update("opencode_api_key", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
            />
            <p className="text-xs text-gray-600">
              Leave empty to keep current key. Get your key at opencode.ai/auth.
            </p>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              OpenCode Server URL
            </label>
            <input
              type="text"
              placeholder="http://opencode:54321"
              value={settings?.opencode_server_url ?? ""}
              onChange={(e) => update("opencode_server_url", e.target.value)}
              onBlur={() => refreshModels()}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
            />
            <p className="text-xs text-gray-600">
              Backend connects via opencode-agent-sdk. Changing URL triggers model re-discovery on blur.
            </p>
          </div>

          <div className="flex items-center justify-between">
            <p className="text-xs text-gray-500">
              {loadingModels ? "Discovering models…" : (
                models?.opencode.models.length
                  ? `${models.opencode.models.length} models from ${models.opencode.providers.length || "?"} providers`
                  : "No models discovered — check server URL"
              )}
            </p>
            <button
              onClick={refreshModels}
              disabled={loadingModels}
              className="flex items-center gap-1 text-xs text-accent-blue hover:text-blue-400 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} className={loadingModels ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">
                Provider ID
              </label>
              <select
                value={settings?.opencode_provider_id ?? "anthropic"}
                onChange={(e) => update("opencode_provider_id", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              >
                {(models?.known_providers ?? []).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
                {models?.opencode.providers
                  .filter((p) => !(models?.known_providers ?? []).some((kp) => kp.id === p.id))
                  .map((p) => (
                    <option key={p.id} value={p.id}>{p.name} (discovered)</option>
                  ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">
                Model ID
              </label>
              <select
                value={settings?.opencode_model ?? ""}
                onChange={(e) => update("opencode_model", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              >
                <option value="">(server default)</option>
                {models?.opencode.models.map((m) => (
                  <option key={`${m.provider_id}/${m.model_id}`} value={m.model_id}>
                    {m.display_name}
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-600">
                Leave empty to use OpenCode server default. Current:{" "}
                <code className="text-accent-purple">{settings?.opencode_model || "(default)"}</code>
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              OpenCode System Prompt (optional override)
            </label>
            <textarea
              rows={4}
              value={settings?.opencode_system_prompt ?? ""}
              onChange={(e) => update("opencode_system_prompt", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 p-3 text-sm text-gray-200 focus:outline-none focus:border-accent-blue resize-y font-mono leading-relaxed"
              placeholder="Optional OpenCode-specific system prompt"
            />
          </div>
        </section>

        {/* System Status */}
        <section className="glass rounded-xl p-6 space-y-4">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
            <Server size={16} />
            System Status
          </h2>
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-lg bg-dark-700 border border-dark-600 p-4">
              <p className="text-xs text-gray-500 mb-1">Backend</p>
              <div className="flex items-center gap-2">
                <Activity size={14} className={health?.status === "ok" ? "text-green-400" : "text-red-400"} />
                <span className="text-sm capitalize">{health?.status ?? "checking…"}</span>
              </div>
            </div>
            <div className="rounded-lg bg-dark-700 border border-dark-600 p-4">
              <p className="text-xs text-gray-500 mb-1">LLM Server</p>
              <div className="flex items-center gap-2">
                <Activity size={14} className={health?.llm === "connected" ? "text-green-400" : "text-red-400"} />
                <span className="text-sm capitalize">{health?.llm ?? "checking…"}</span>
              </div>
            </div>
          </div>
          <p className="text-xs text-gray-600">Version: {health?.version ?? "—"}</p>
        </section>
      </div>
    </div>
  );
}
