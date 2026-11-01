import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Moon, ShieldAlert } from "lucide-react";
import { api } from "../../api/client";
import type { NightwatchBrief, NightwatchOverview } from "../../types/nightwatch";

const LAST_SEEN_KEY = "nightwatch.last_seen";

export default function ShiftHandover() {
  const navigate = useNavigate();
  const [brief, setBrief] = useState<NightwatchBrief | null>(null);
  const [overview, setOverview] = useState<NightwatchOverview | null>(null);

  useEffect(() => {
    let cancelled = false;
    const since = localStorage.getItem(LAST_SEEN_KEY) || undefined;
    Promise.all([api.nightwatchBrief(since), api.nightwatchOverview()])
      .then(([data, nextOverview]) => {
        if (cancelled) return;
        setBrief(data);
        setOverview(nextOverview);
        if (data.available) {
          localStorage.setItem(LAST_SEEN_KEY, new Date().toISOString());
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBrief(null);
          setOverview(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!brief || !brief.available) return null;

  const { counts } = brief;
  if (counts.incidents === 0 && counts.escalated === 0) {
    return (
      <button
        type="button"
        onClick={() => navigate("/nightwatch")}
        className="s-card w-full max-w-xl text-left transition-colors hover:border-dark-500"
      >
        <div className="flex items-center gap-3 px-4 py-3">
          <span className="s-status s-status-healthy" aria-hidden="true" />
          <Moon size={16} className="text-gray-400" />
          <span className="text-sm text-gray-300">
            Nightwatch watched {overview?.assignments ?? brief.partitions.length} partition
            {(overview?.assignments ?? brief.partitions.length) === 1 ? "" : "s"} in shadow
            mode. No new incidents.
          </span>
          <ArrowRight size={14} className="ml-auto text-gray-500" />
        </div>
      </button>
    );
  }

  return (
    <div className="s-card w-full max-w-xl text-left">
      <div className="s-card__header">
        <div>
          <p className="s-eyebrow">Shift handover</p>
          <h2 className="s-card__title">While you were away</h2>
        </div>
        <span className="s-badge">shadow</span>
      </div>
      <div className="s-card__body space-y-3">
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="s-badge">resolved {counts.resolved}</span>
          <span className="s-badge">handed off {counts.handed_off}</span>
          <span className="s-badge">escalated {counts.escalated}</span>
          <span className="s-badge">recommendations {counts.recommendations}</span>
        </div>

        <div className="space-y-1.5">
          {brief.notable_events.slice(0, 4).map((event) => (
            <div key={event.id} className="flex items-start gap-2 text-xs text-gray-400">
              <ShieldAlert size={12} className="mt-0.5 shrink-0 text-amber-400" />
              <span className="min-w-0 flex-1 truncate">{event.summary}</span>
              <span className="shrink-0 text-gray-600">
                {event.incident_partition || ""}
              </span>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            className="s-btn s-btn-primary"
            onClick={() => navigate("/nightwatch")}
          >
            Open Nightwatch
            <ArrowRight size={14} />
          </button>
          <span className="text-xs text-gray-500">
            Shadow mode only — no actions were executed.
          </span>
        </div>
      </div>
    </div>
  );
}
