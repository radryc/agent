import { useEffect, useState } from "react";
import { api, AgentSettings, AgentModelsResponse, AuthStatus, DeviceLoginStart, PersonaStatus } from "../api/client";
import AgentAvatar from "../components/chat/AgentAvatar";
import { Activity, Cpu, Server, Save, Bot, KeyRound, Terminal, RefreshCw, Code2, FolderCode, Zap, ShieldCheck, LogIn, LogOut, ExternalLink } from "lucide-react";

export default function SettingsPage() {
  const [health, setHealth] = useState<{ status: string; llm: string; version: string } | null>(null);
  const [agentModel, setAgentModel] = useState("gemma-4-E4B-it-GGUF (Q4_K_M)");
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [models, setModels] = useState<AgentModelsResponse | null>(null);
  const [personas, setPersonas] = useState<PersonaStatus[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [deviceLogin, setDeviceLogin] = useState<DeviceLoginStart | null>(null);
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);

  function loadData() {
    api.health().then(setHealth).catch(() => {});
    api.agentInfo().then((a) => setAgentModel(a.model)).catch(() => {});
    api.getSettings().then(setSettings).catch(() => {});
    api.agentStatus().then((s) => setPersonas(s.personas)).catch(() => {});
    api.authStatus().then(setAuthStatus).catch(() => {});
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

  useEffect(() => {
    if (!deviceLogin) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const result = await api.pollDeviceLogin();
        if (cancelled) return;
        if (result.status === "authorized") {
          setDeviceLogin(null);
          setAuthError("");
          api.authStatus().then(setAuthStatus).catch(() => {});
          return;
        }
        if (result.status === "expired" || result.status === "denied") {
          setDeviceLogin(null);
          setAuthError(
            result.status === "expired"
              ? "SSO login expired. Try again."
              : "SSO login was denied."
          );
          return;
        }
        timer = window.setTimeout(poll, (result.interval || deviceLogin.interval) * 1000);
      } catch {
        if (!cancelled) {
          setAuthError("SSO polling failed. Check the backend and OIDC settings.");
          setDeviceLogin(null);
        }
      }
    };

    timer = window.setTimeout(poll, deviceLogin.interval * 1000);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [deviceLogin]);

  function startSso() {
    window.location.href = `/api/auth/login?next=${encodeURIComponent("/settings")}`;
  }

  async function signOutSso() {
    setAuthBusy(true);
    setAuthError("");
    try {
      await api.logoutSso();
      sessionStorage.removeItem("lagent_skip_sso");
      window.location.href = "/";
    } catch {
      setAuthError("Could not sign out.");
      setAuthBusy(false);
    }
  }

  async function save() {
    if (!settings) return;
    setSaving(true);
    try {
      const payload: Partial<AgentSettings> = { ...settings };
      delete payload.workspace;
      if (!payload.gemini_api_key?.trim()) {
        delete payload.gemini_api_key;
      }
      if (!payload.opencode_api_key?.trim()) {
        delete payload.opencode_api_key;
      }
      if (!payload.steve_llm_api_key?.trim()) {
        delete payload.steve_llm_api_key;
      }
      if (!payload.agent_oidc_client_secret?.trim()) {
        delete payload.agent_oidc_client_secret;
      }
      if (!payload.agent_oidc_token?.trim()) {
        delete payload.agent_oidc_token;
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
            Otto — SRE Persona
          </h2>
          <div className="flex items-center gap-4">
            <AgentAvatar size="lg" />
            <div>
              <p className="font-semibold">Outage Otto</p>
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
              Otto System Prompt (SRE)
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

        {/* Steve — Software Engineer Persona */}
        <section className="glass rounded-xl p-6 space-y-5">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
            <Code2 size={16} className="text-accent-purple" />
            Steve — Software Engineer
          </h2>
          <div className="flex items-center gap-4">
            <AgentAvatar size="lg" persona="steve" />
            <div>
              <p className="font-semibold">Software Steve</p>
              <p className="text-xs text-gray-500 mt-0.5">
                Coding persona — edits, commits and pushes code in the MonoFS workspace
              </p>
            </div>
          </div>

          {/* Engine */}
          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">Coding Engine</label>
            <select
              value={settings?.steve_engine ?? ""}
              onChange={(e) => update("steve_engine", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-purple"
            >
              <option value="">Auto — OpenCode → External API → Local</option>
              <option value="opencode">OpenCode server</option>
              <option value="deepseek">External API (OpenAI-compatible / DeepSeek)</option>
              <option value="local">Local Gemma (shared with Otto)</option>
            </select>
            <p className="text-xs text-gray-600">
              Detected:{" "}
              {personas.find((p) => p.id === "steve") ? (
                <code className="text-accent-purple">
                  {personas.find((p) => p.id === "steve")!.engine.label} —{" "}
                  {personas.find((p) => p.id === "steve")!.engine.available ? "available" : "unavailable"}
                </code>
              ) : (
                "checking…"
              )}
            </p>
          </div>

          {/* External API profile */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">API Base URL</label>
              <input
                type="text"
                value={settings?.steve_llm_base_url ?? "https://api.deepseek.com"}
                onChange={(e) => update("steve_llm_base_url", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-purple"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Model</label>
              <input
                type="text"
                value={settings?.steve_llm_model ?? "deepseek-chat"}
                onChange={(e) => update("steve_llm_model", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-purple"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              External API Key
              <span className="ml-1 normal-case text-gray-600">(DeepSeek or any OpenAI-compatible provider)</span>
            </label>
            <input
              type="password"
              placeholder={
                settings?.steve_llm_api_key_set
                  ? `Configured: ${settings.steve_llm_api_key_masked ?? "(hidden)"}`
                  : "Paste the API key for Steve's coding engine"
              }
              value={settings?.steve_llm_api_key ?? ""}
              onChange={(e) => update("steve_llm_api_key", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-purple"
            />
            <p className="text-xs text-gray-600">
              Leave empty to keep the current key. Used when the coding engine resolves to the external API.
              To use OpenCode instead, configure the OpenCode server URL below.
            </p>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              Steve System Prompt (Software Engineer)
            </label>
            <textarea
              rows={8}
              value={settings?.steve_system_prompt ?? ""}
              onChange={(e) => update("steve_system_prompt", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 p-3 text-sm text-gray-200 focus:outline-none focus:border-accent-purple resize-y font-mono leading-relaxed"
              placeholder="Loading…"
            />
          </div>

          {/* Switching behaviour */}
          <div className="space-y-3 pt-1">
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={settings?.auto_switch_enabled ?? true}
                onChange={(e) => update("auto_switch_enabled", e.target.checked)}
                style={{ accentColor: "var(--color-accent-purple, #9333ea)" }}
                className="mt-0.5"
              />
              <span>
                <span className="text-sm text-gray-200">Auto-switch Otto ↔ Steve</span>
                <span className="block text-xs text-gray-600">
                  Route each message by context (mentions, topic classifier, sticky conversation).
                  Disable to control the persona manually from the sidebar.
                </span>
              </span>
            </label>

            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={settings?.auto_push_enabled ?? false}
                onChange={(e) => update("auto_push_enabled", e.target.checked)}
                style={{ accentColor: "var(--color-accent-purple, #9333ea)" }}
                className="mt-0.5"
              />
              <span>
                <span className="text-sm text-gray-200 flex items-center gap-1.5">
                  <Zap size={12} className="text-amber-400" />
                  Autonomous push
                </span>
                <span className="block text-xs text-gray-600">
                  When enabled, Steve can commit and push workspace changes without asking first.
                  Leave off to require explicit approval (confirm=true) for every commit/push.
                </span>
              </span>
            </label>
          </div>

          {/* MonoFS workspace */}
          <div className="rounded-lg bg-dark-700 border border-dark-600 p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold flex items-center gap-2 text-gray-200">
                <FolderCode size={14} className="text-accent-purple" />
                MonoFS Workspace
              </h3>
              <span
                className={`text-xs font-medium ${
                  settings?.workspace?.mounted ? "text-green-400" : "text-amber-400"
                }`}
              >
                {settings?.workspace?.mounted
                  ? `mounted · ${settings.workspace.socket ? "session socket ok" : "no socket"}`
                  : "not mounted"}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <label className="block text-xs text-gray-500 uppercase tracking-wide">Router</label>
                <input
                  type="text"
                  placeholder="monofs-router:8081 or host:port"
                  value={settings?.monofs_router ?? ""}
                  onChange={(e) => update("monofs_router", e.target.value)}
                  className="w-full rounded-lg bg-dark-800 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-accent-purple"
                />
              </div>
              <div className="space-y-2">
                <label className="block text-xs text-gray-500 uppercase tracking-wide">Mount</label>
                <input
                  type="text"
                  value={settings?.monofs_mount ?? "/workspace"}
                  onChange={(e) => update("monofs_mount", e.target.value)}
                  className="w-full rounded-lg bg-dark-800 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-accent-purple"
                />
              </div>
              <div className="space-y-2">
                <label className="block text-xs text-gray-500 uppercase tracking-wide">Overlay</label>
                <input
                  type="text"
                  value={settings?.monofs_overlay ?? "/data/monofs-overlay"}
                  onChange={(e) => update("monofs_overlay", e.target.value)}
                  className="w-full rounded-lg bg-dark-800 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-accent-purple"
                />
              </div>
            </div>
            <p className="text-xs text-gray-600">
              The router address is read at container start (MONOFS_ROUTER) to mount the writable
              workspace. Changes here apply to tools that shell out to monofs-session; restart the
              backend to (re)mount after changing the router.
            </p>
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

        {/* SSO / OIDC */}
        <section className="glass rounded-xl p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
              <ShieldCheck size={16} className="text-emerald-400" />
              SSO / OIDC
            </h2>
            <span
              className={`text-xs font-medium ${
                authStatus?.authenticated_as_user || authStatus?.source === "device"
                  ? "text-green-400"
                  : "text-amber-400"
              }`}
            >
              {authStatus?.authenticated
                ? authStatus.authenticated_as_user || authStatus.source === "device"
                  ? `signed in · ${authStatus.subject || "SSO user"}`
                  : `machine identity${authStatus.source ? ` · ${authStatus.source}` : ""}`
                : "not signed in"}
            </span>
          </div>

          <p className="text-xs text-gray-600">
            Used by Doctor log/trace queries, Guardian API calls, and MonoFS tools.
            Client credentials are the machine identity and are always available.
            Sign in with SSO to use a per-user identity so Doctor returns the logs,
            traces, and partitions that <em>you</em> are allowed to see.
          </p>

          {authStatus?.authenticated && (
            <div className="rounded-lg bg-dark-700 border border-dark-600 p-3 flex items-center justify-between gap-4">
              <div className="text-xs text-gray-400 space-y-0.5">
                {authStatus.subject && <div>Subject: <code className="text-gray-200">{authStatus.subject}</code></div>}
                {authStatus.issuer && <div>Issuer: <code className="text-gray-200 break-all">{authStatus.issuer}</code></div>}
                {authStatus.expires_at && (
                  <div>Expires: {new Date(authStatus.expires_at * 1000).toLocaleString()}</div>
                )}
              </div>
              <button
                onClick={signOutSso}
                disabled={authBusy}
                className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 disabled:opacity-50"
              >
                <LogOut size={12} /> Sign out
              </button>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Issuer</label>
              <input
                type="text"
                placeholder="http://authentik.authentik.svc.cluster.local:9000/application/o/monofs/"
                value={settings?.agent_oidc_issuer ?? ""}
                onChange={(e) => update("agent_oidc_issuer", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Token URL</label>
              <input
                type="text"
                placeholder="http://authentik.authentik.svc.cluster.local:9000/application/o/token/"
                value={settings?.agent_oidc_token_url ?? ""}
                onChange={(e) => update("agent_oidc_token_url", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Device Authorization URL</label>
              <input
                type="text"
                placeholder="http://authentik.authentik.svc.cluster.local:9000/application/o/device/"
                value={settings?.agent_oidc_device_authorization_url ?? ""}
                onChange={(e) => update("agent_oidc_device_authorization_url", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Authorization URL</label>
              <input
                type="text"
                placeholder="http://localhost:9020/application/o/authorize/"
                value={settings?.agent_oidc_authorization_url ?? ""}
                onChange={(e) => update("agent_oidc_authorization_url", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Redirect URL</label>
              <input
                type="text"
                placeholder="http://localhost:9191/api/auth/callback"
                value={settings?.agent_oidc_redirect_url ?? ""}
                onChange={(e) => update("agent_oidc_redirect_url", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">
                Browser-visible IdP base URL
                <span className="ml-1 normal-case text-gray-600">(used to rewrite the device verification link)</span>
              </label>
              <input
                type="text"
                placeholder="http://localhost:9020"
                value={settings?.agent_oidc_public_base_url ?? ""}
                onChange={(e) => update("agent_oidc_public_base_url", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
              <p className="text-xs text-gray-600">
                The backend calls Authentik over its in-cluster service. Set this to the
                URL your browser can open (for local kind, usually
                <code className="ml-1 text-emerald-300">http://localhost:9020</code>).
              </p>
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Client ID</label>
              <input
                type="text"
                placeholder="monofs"
                value={settings?.agent_oidc_client_id ?? ""}
                onChange={(e) => update("agent_oidc_client_id", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Client Secret</label>
              <input
                type="password"
                placeholder={
                  settings?.agent_oidc_client_secret_set
                    ? `Configured: ${settings.agent_oidc_client_secret_masked ?? "(hidden)"}`
                    : "Optional for device flow"
                }
                value={settings?.agent_oidc_client_secret ?? ""}
                onChange={(e) => update("agent_oidc_client_secret", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Audience</label>
              <input
                type="text"
                placeholder="monofs"
                value={settings?.agent_oidc_audience ?? ""}
                onChange={(e) => update("agent_oidc_audience", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Scope</label>
              <input
                type="text"
                placeholder="openid"
                value={settings?.agent_oidc_scope ?? ""}
                onChange={(e) => update("agent_oidc_scope", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">
                Static token
                <span className="ml-1 normal-case text-gray-600">(break-glass / pre-issued bearer token)</span>
              </label>
              <input
                type="password"
                placeholder={
                  settings?.agent_oidc_token_set
                    ? `Configured: ${settings.agent_oidc_token_masked ?? "(hidden)"}`
                    : "Optional — takes precedence over OIDC flows"
                }
                value={settings?.agent_oidc_token ?? ""}
                onChange={(e) => update("agent_oidc_token", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-xs text-gray-200 focus:outline-none focus:border-emerald-400"
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={startSso}
              disabled={authBusy || !settings?.agent_oidc_device_authorization_url}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium transition-colors"
            >
              <LogIn size={13} />
              {authBusy ? "Starting…" : "Sign in with SSO"}
            </button>
            <button
              onClick={() => api.authStatus().then(setAuthStatus).catch(() => {})}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 transition-colors"
            >
              <RefreshCw size={12} /> Refresh status
            </button>
          </div>

          {deviceLogin && (
            <div className="rounded-lg bg-dark-700 border border-emerald-700/50 p-4 space-y-2">
              <p className="text-xs text-gray-400">
                Open the verification page and enter this code:
              </p>
              <div className="text-2xl font-mono tracking-widest text-emerald-300">
                {deviceLogin.user_code}
              </div>
              <a
                href={deviceLogin.verification_uri_complete || deviceLogin.verification_uri}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-emerald-400 hover:text-emerald-300"
              >
                Open verification page <ExternalLink size={11} />
              </a>
              <p className="text-xs text-gray-600">
                Waiting for approval… the code expires in {deviceLogin.expires_in}s.
              </p>
            </div>
          )}

          {authError && <p className="text-xs text-red-400">{authError}</p>}
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
