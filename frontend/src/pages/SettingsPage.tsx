import { useEffect, useState } from "react";
import { api, AgentSettings } from "../api/client";
import AgentAvatar from "../components/chat/AgentAvatar";
import { Activity, Cpu, Server, Save, Image, Bot } from "lucide-react";

const IMAGE_GENERATORS = ["Z Image Turbo"];
const VIDEO_GENERATORS = ["LTX-2 2.3 Distilled", "Enhanced Lightning v2"];

export default function SettingsPage() {
  const [health, setHealth] = useState<{ status: string; llm: string; version: string } | null>(null);
  const [agentModel, setAgentModel] = useState("gemma-4-E4B-it-GGUF (Q4_K_M)");
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    api.agentInfo().then((a) => setAgentModel(a.model)).catch(() => {});
    api.getSettings().then(setSettings).catch(() => {});
  }, []);

  function update<K extends keyof AgentSettings>(key: K, value: AgentSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function save() {
    if (!settings) return;
    setSaving(true);
    try {
      const updated = await api.saveSettings(settings);
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
              <p className="font-semibold">Lagent</p>
              <div className="flex items-center gap-2 mt-0.5">
                <Cpu size={12} className="text-accent-purple" />
                <span className="text-xs text-gray-500">{agentModel}</span>
              </div>
            </div>
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

        {/* Image Generation */}
        <section className="glass rounded-xl p-6 space-y-5">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-200">
            <Image size={16} className="text-accent-purple" />
            Image Generation (wan2gp)
          </h2>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">wan2gp URL</label>
            <input
              type="text"
              value={settings?.wan2gp_url ?? ""}
              onChange={(e) => update("wan2gp_url", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Image Generator</label>
              <select
                value={settings?.image_generator ?? ""}
                onChange={(e) => update("image_generator", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              >
                {IMAGE_GENERATORS.map((g) => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Video Generator</label>
              <select
                value={settings?.video_generator ?? ""}
                onChange={(e) => update("video_generator", e.target.value)}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              >
                {VIDEO_GENERATORS.map((g) => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Width</label>
              <input
                type="number"
                step={8}
                min={256}
                max={2048}
                value={settings?.image_width ?? 1280}
                onChange={(e) => update("image_width", parseInt(e.target.value))}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Height</label>
              <input
                type="number"
                step={8}
                min={256}
                max={2048}
                value={settings?.image_height ?? 720}
                onChange={(e) => update("image_height", parseInt(e.target.value))}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs text-gray-500 uppercase tracking-wide">Seed (-1 random)</label>
              <input
                type="number"
                min={-1}
                value={settings?.image_seed ?? -1}
                onChange={(e) => update("image_seed", parseInt(e.target.value))}
                className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-gray-500 uppercase tracking-wide">
              Default LoRAs
              <span className="ml-1 normal-case text-gray-600">(comma-separated names)</span>
            </label>
            <input
              type="text"
              placeholder="e.g. lego, watercolor"
              value={settings?.image_loras ?? ""}
              onChange={(e) => update("image_loras", e.target.value)}
              className="w-full rounded-lg bg-dark-700 border border-dark-600 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
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

