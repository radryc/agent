import {
  Activity,
  ArrowUpRight,
  CircleAlert,
  FlaskConical,
  ShieldAlert,
} from "lucide-react";

export interface VerdictCandidate {
  partition?: string;
  service?: string;
  score?: number;
  confidence?: number;
  rationale?: string;
}

export interface VerdictImprovement {
  title?: string;
  category?: string;
  rationale?: string;
  proposed_change?: string;
}

export interface Verdict {
  summary?: string;
  root_cause_partition?: string;
  root_cause_service?: string;
  confidence?: number;
  cross_partition?: boolean;
  escalate?: boolean;
  escalation_target?: string;
  escalation_reason?: string;
  recommended_action?: string;
  recommendation_target?: string;
  rationale?: string;
  falsification?: string;
  candidates?: VerdictCandidate[];
  improvements?: VerdictImprovement[];
  otto_response?: string;
}

const ACTION_STYLES: Record<string, string> = {
  none: "border-dark-500 bg-dark-700 text-gray-300",
  notify: "border-sky-500/40 bg-sky-500/15 text-sky-300",
  ack: "border-sky-500/40 bg-sky-500/15 text-sky-300",
  silence: "border-amber-500/40 bg-amber-500/15 text-amber-300",
  escalate: "border-red-500/40 bg-red-500/15 text-red-300",
  handoff: "border-orange-500/40 bg-orange-500/15 text-orange-300",
  reconcile: "border-violet-500/40 bg-violet-500/15 text-violet-300",
  restart: "border-violet-500/40 bg-violet-500/15 text-violet-300",
  scale: "border-violet-500/40 bg-violet-500/15 text-violet-300",
  rollback: "border-violet-500/40 bg-violet-500/15 text-violet-300",
  patch_code: "border-purple-500/40 bg-purple-500/15 text-purple-300",
};

function pct(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-dark-600/70 px-4 py-3">
      <h4 className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
        {title}
      </h4>
      {children}
    </section>
  );
}

export default function VerdictCard({ verdict }: { verdict: Verdict }) {
  const action = (verdict.recommended_action || "none").toLowerCase();
  const actionStyle =
    ACTION_STYLES[action] ?? "border-dark-500 bg-dark-700 text-gray-300";
  const confidence = verdict.confidence ?? 0;

  return (
    <div className="overflow-hidden rounded-xl border border-dark-500 bg-dark-900/60">
      <div className="px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] ${actionStyle}`}
          >
            <CircleAlert size={11} />
            {action.replace("_", " ")}
          </span>
          {verdict.escalate && (
            <span className="inline-flex items-center gap-1 rounded-full border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-red-300">
              <ShieldAlert size={11} />
              escalate
            </span>
          )}
          {verdict.cross_partition && (
            <span className="rounded-full border border-orange-500/40 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-orange-300">
              cross-partition
            </span>
          )}
        </div>
        <p className="text-sm leading-relaxed text-gray-100">
          {verdict.summary || verdict.otto_response || "No summary returned."}
        </p>
      </div>

      <div className="grid gap-0 border-t border-dark-600/70 sm:grid-cols-2">
        <div className="px-4 py-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
            Root cause
          </div>
          <div className="mt-1 break-words font-mono text-xs text-gray-200">
            {verdict.root_cause_service || "undetermined"}
            {verdict.root_cause_partition && (
              <span className="text-gray-500">
                {" "}
                in {verdict.root_cause_partition}
              </span>
            )}
          </div>
        </div>
        <div className="px-4 py-3 sm:border-l sm:border-dark-600/70">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
              Confidence
            </span>
            <span className="text-xs font-semibold text-gray-200">
              {pct(confidence)}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-dark-700">
            <div
              className="h-full rounded-full bg-accent-cyan"
              style={{ width: `${Math.max(2, Math.min(100, confidence * 100))}%` }}
            />
          </div>
        </div>
      </div>

      {verdict.rationale && (
        <Section title="Why">
          <p className="text-xs leading-relaxed text-gray-300">
            {verdict.rationale}
          </p>
        </Section>
      )}

      {(verdict.escalation_target || verdict.recommendation_target) && (
        <Section title="Route">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-300">
            {verdict.recommendation_target && (
              <span className="inline-flex items-center gap-1">
                <ArrowUpRight size={12} className="text-gray-500" />
                {verdict.recommendation_target}
              </span>
            )}
            {verdict.escalate && verdict.escalation_target && (
              <span className="inline-flex items-center gap-1 text-red-300">
                <ShieldAlert size={12} />
                {verdict.escalation_target}
              </span>
            )}
          </div>
          {verdict.escalation_reason && (
            <p className="mt-1 text-xs text-gray-400">
              {verdict.escalation_reason}
            </p>
          )}
        </Section>
      )}

      {!!verdict.candidates?.length && (
        <Section title="Candidates">
          <ul className="space-y-1.5">
            {verdict.candidates.slice(0, 5).map((candidate, index) => (
              <li key={`${candidate.service}-${index}`} className="text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-gray-200">
                    {candidate.service || candidate.partition || "unnamed"}
                  </span>
                  <span className="text-gray-500">
                    score {candidate.score?.toFixed(2) ?? "—"} ·{" "}
                    {pct(candidate.confidence)}
                  </span>
                </div>
                {candidate.rationale && (
                  <p className="mt-0.5 text-gray-400">{candidate.rationale}</p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {verdict.falsification && (
        <Section title="What would prove this wrong">
          <p className="flex gap-1.5 text-xs leading-relaxed text-gray-300">
            <FlaskConical size={13} className="mt-0.5 shrink-0 text-gray-500" />
            <span>{verdict.falsification}</span>
          </p>
        </Section>
      )}

      {!!verdict.improvements?.length && (
        <Section title="Observability follow-ups">
          <ul className="space-y-1.5">
            {verdict.improvements.slice(0, 6).map((item, index) => (
              <li key={`${item.title}-${index}`} className="text-xs">
                <span className="mr-1 inline-flex items-center gap-1 font-medium text-gray-200">
                  <Activity size={11} className="text-accent-cyan" />
                  {item.title || "Improvement"}
                </span>
                {item.category && (
                  <span className="text-gray-500">({item.category})</span>
                )}
                {(item.proposed_change || item.rationale) && (
                  <p className="mt-0.5 text-gray-400">
                    {item.proposed_change || item.rationale}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
