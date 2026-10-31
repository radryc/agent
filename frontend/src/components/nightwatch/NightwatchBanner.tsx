import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Moon } from "lucide-react";
import { api } from "../../api/client";
import type { NightwatchBrief, NightwatchOverview } from "../../types/nightwatch";

export default function NightwatchBanner() {
  const navigate = useNavigate();
  const [brief, setBrief] = useState<NightwatchBrief | null>(null);
  const [overview, setOverview] = useState<NightwatchOverview | null>(null);

  useEffect(() => {
    let cancelled = false;
    const since = localStorage.getItem("nightwatch.last_seen") || undefined;
    Promise.all([api.nightwatchBrief(since), api.nightwatchOverview()])
      .then(([nextBrief, nextOverview]) => {
        if (cancelled) return;
        setBrief(nextBrief);
        setOverview(nextOverview);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!brief?.available || !overview?.available) return null;
  const noteworthy =
    brief.counts.resolved +
    brief.counts.handed_off +
    brief.counts.escalated +
    brief.counts.recommendations;
  if (noteworthy === 0) return null;

  return (
    <button
      type="button"
      onClick={() => navigate("/nightwatch")}
      className="flex w-full items-center gap-2 border-b border-violet-500/20 bg-violet-500/5 px-4 py-1.5 text-left text-xs text-violet-200 transition-colors hover:bg-violet-500/10"
    >
      <Moon size={12} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">
        Nightwatch: {brief.counts.resolved} resolved · {brief.counts.handed_off} handed off ·{" "}
        {brief.counts.escalated} escalation{brief.counts.escalated === 1 ? "" : "s"}
      </span>
      <span className="shrink-0 text-violet-300/70">shadow</span>
      <ArrowRight size={12} className="shrink-0" />
    </button>
  );
}
