import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  ChevronRight,
  CircleCheck,
  Clock,
  Eye,
  GitBranch,
  Hand,
  Moon,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { api } from "../api/client";
import type {
  NightwatchAssignment,
  NightwatchEvent,
  NightwatchEvidence,
  NightwatchIncident,
  NightwatchIncidentDetail,
  NightwatchOverview,
} from "../types/nightwatch";

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function severityClass(severity: string): string {
  switch (severity.toLowerCase()) {
    case "critical":
    case "page":
      return "s-status-critical";
    case "warning":
      return "s-status-warning";
    default:
      return "s-status-unknown";
  }
}

function statusClass(status: string): string {
  switch (status) {
    case "resolved":
      return "s-status-healthy";
    case "investigating":
      return "s-status-pending";
    case "failed":
      return "s-status-critical";
    default:
      return "s-status-unknown";
  }
}

function EventIcon({ event }: { event: NightwatchEvent }) {
  const props = { size: 14, className: "shrink-0" };
  switch (event.type) {
    case "alert.firing":
      return <AlertTriangle {...props} className="shrink-0 text-amber-400" />;
    case "investigation.started":
      return <Search {...props} className="shrink-0 text-blue-400" />;
    case "investigation.completed":
    case "finding.local":
      return <Sparkles {...props} className="shrink-0 text-violet-400" />;
    case "finding.cross_partition":
      return <GitBranch {...props} className="shrink-0 text-amber-400" />;
    case "escalation.recommended":
      return <ShieldAlert {...props} className="shrink-0 text-rose-400" />;
    case "incident.resolved":
      return <CircleCheck {...props} className="shrink-0 text-emerald-400" />;
    case "investigation.failed":
      return <AlertTriangle {...props} className="shrink-0 text-rose-400" />;
    default:
      return <Activity {...props} className="shrink-0 text-gray-400" />;
  }
}

function EvidenceCard({ evidence }: { evidence: NightwatchEvidence }) {
  return (
    <details className="rounded-lg border border-dark-600 bg-dark-800/60 px-3 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-gray-300">
        <span className="rounded bg-dark-600 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-gray-400">
          {evidence.kind}
        </span>
        <span className="min-w-0 flex-1 truncate">{evidence.query}</span>
        <span className="text-gray-600">{evidence.source}</span>
      </summary>
      <div className="mt-2 space-y-1 border-t border-dark-600 pt-2 text-xs">
        <p className="whitespace-pre-wrap text-gray-300">{evidence.result_summary}</p>
        <p className="font-mono text-[10px] text-gray-600">
          {evidence.from_ts ? formatDateTime(evidence.from_ts) : "—"} →{" "}
          {evidence.to_ts ? formatDateTime(evidence.to_ts) : "—"} · hash{" "}
          {evidence.result_hash.slice(0, 12)}
        </p>
      </div>
    </details>
  );
}

function IncidentDetail({ incident }: { incident: NightwatchIncidentDetail }) {
  const confidence = Math.max(0, Math.min(1, incident.confidence ?? 0));
  const escalated =
    incident.escalation_status === "recommended" || incident.escalation_status === "escalated";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="s-eyebrow">{incident.partition}</p>
          <h2 className="s-title text-xl">{incident.title}</h2>
          <p className="s-meta">
            Opened {formatDateTime(incident.started_at)} · last update{" "}
            {formatDateTime(incident.updated_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`s-status ${severityClass(incident.severity)}`}>
            {incident.severity}
          </span>
          <span className={`s-status ${statusClass(incident.status)}`}>{incident.status}</span>
          {incident.handoff_status === "handed_off" && (
            <span className="s-badge">handoff</span>
          )}
          {escalated && <span className="s-badge">escalation recommended</span>}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="s-card">
          <div className="s-card__header">
            <h3 className="s-card__title">Agent assessment</h3>
            <span className="s-badge">shadow</span>
          </div>
          <div className="s-card__body space-y-3">
            <div className="flex items-center gap-3">
              <Moon size={16} className="text-violet-400" />
              <div className="min-w-0">
                <p className="text-xs uppercase tracking-wide text-gray-500">Root-cause candidate</p>
                <p className="truncate text-sm text-gray-200">
                  {incident.root_cause_service || "Not enough evidence yet"}
                </p>
                {incident.root_cause_partition &&
                  incident.root_cause_partition !== incident.partition && (
                    <p className="text-xs text-amber-400">
                      outside assignment · {incident.root_cause_partition}
                    </p>
                  )}
              </div>
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
                <span>Confidence</span>
                <span>{Math.round(confidence * 100)}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-dark-600">
                <div
                  className="h-full rounded-full bg-violet-500 transition-all"
                  style={{ width: `${Math.round(confidence * 100)}%` }}
                />
              </div>
            </div>

            {incident.summary && (
              <p className="text-sm leading-relaxed text-gray-300">{incident.summary}</p>
            )}

            {escalated && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                <p className="font-medium">Escalation recommended</p>
                <p className="mt-1 text-amber-200/80">
                  {incident.escalation_reason || "Needs human review."}
                </p>
                {incident.escalation_target && (
                  <p className="mt-1 font-mono text-[10px] text-amber-300/70">
                    target: {incident.escalation_target}
                  </p>
                )}
              </div>
            )}

            <div className="rounded-lg border border-dark-600 bg-dark-800/60 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wide text-gray-500">
                Recommendation only
              </p>
              <p className="text-sm text-gray-200">
                {incident.recommended_action || "none"}
                {incident.recommendation_target ? ` → ${incident.recommendation_target}` : ""}
              </p>
              <p className="mt-1 text-xs text-gray-500">
                Shadow mode never executes actions.
              </p>
            </div>

            {incident.investigation_error && (
              <p className="text-xs text-rose-400">{incident.investigation_error}</p>
            )}
          </div>
        </div>

        <div className="s-card">
          <div className="s-card__header">
            <h3 className="s-card__title">Alerts</h3>
            <span className="s-badge">{incident.alerts.length}</span>
          </div>
          <div className="s-card__body space-y-2">
            {incident.alerts.length === 0 && (
              <p className="s-empty">No alerts attached.</p>
            )}
            {incident.alerts.map((alert) => (
              <div
                key={alert.fingerprint}
                className="rounded-lg border border-dark-600 bg-dark-800/60 px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm text-gray-200">{alert.alertname}</p>
                  <span className={`s-status ${statusClass(alert.status)}`}>
                    {alert.status}
                  </span>
                </div>
                <p className="mt-1 text-xs text-gray-500">
                  {alert.severity} · started {formatTime(alert.starts_at)}
                </p>
                {alert.annotations.summary && (
                  <p className="mt-1 text-xs text-gray-400">{alert.annotations.summary}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="s-card">
          <div className="s-card__header">
            <h3 className="s-card__title">Timeline</h3>
            <span className="s-badge">{incident.events.length} events</span>
          </div>
          <div className="s-card__body">
            <ol className="space-y-3">
              {incident.events.map((event) => (
                <li key={event.id} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <EventIcon event={event} />
                    <span className="mt-1 h-full w-px bg-dark-600" />
                  </div>
                  <div className="min-w-0 pb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-gray-200">{event.summary}</span>
                      <span className="text-[10px] text-gray-600">{formatTime(event.ts)}</span>
                    </div>
                    <p className="text-[10px] uppercase tracking-wide text-gray-600">
                      {event.type} · {event.actor} · seq {event.seq}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>

        <div className="s-card">
          <div className="s-card__header">
            <h3 className="s-card__title">Evidence</h3>
            <span className="s-badge">{incident.evidence.length}</span>
          </div>
          <div className="s-card__body space-y-2">
            {incident.evidence.length === 0 && (
              <p className="s-empty">No evidence collected yet.</p>
            )}
            {incident.evidence.map((item) => (
              <EvidenceCard key={item.id} evidence={item} />
            ))}
          </div>
        </div>
      </div>

      {incident.findings.length > 0 && (
        <div className="s-card">
          <div className="s-card__header">
            <h3 className="s-card__title">Candidates</h3>
            <span className="s-badge">{incident.findings.length}</span>
          </div>
          <div className="s-card__body">
            <div className="s-table-wrap">
              <table className="s-table">
                <thead>
                  <tr>
                    <th>Partition</th>
                    <th>Service</th>
                    <th>Score</th>
                    <th>Handoff</th>
                  </tr>
                </thead>
                <tbody>
                  {incident.findings.map((finding) => (
                    <tr key={finding.id}>
                      <td>{finding.candidate_partition}</td>
                      <td className="font-mono text-xs">{finding.candidate_service}</td>
                      <td>{Math.round(finding.score * 100)}%</td>
                      <td>{finding.handoff_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function NightwatchPage() {
  const [overview, setOverview] = useState<NightwatchOverview | null>(null);
  const [assignments, setAssignments] = useState<NightwatchAssignment[]>([]);
  const [incidents, setIncidents] = useState<NightwatchIncident[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<NightwatchIncidentDetail | null>(null);
  const [activity, setActivity] = useState<NightwatchEvent[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [partitionFilter, setPartitionFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextOverview, nextAssignments, nextIncidents, nextActivity] = await Promise.all([
        api.nightwatchOverview(),
        api.nightwatchAssignments(),
        api.nightwatchIncidents({
          status: statusFilter || undefined,
          partition: partitionFilter || undefined,
          limit: 100,
        }),
        api.nightwatchActivity({ partition: partitionFilter || undefined, limit: 50 }),
      ]);
      setOverview(nextOverview);
      setAssignments(nextAssignments.assignments || []);
      setIncidents(nextIncidents.incidents || []);
      setActivity(nextActivity.activity || []);
      setError("");
      setLoading(false);
      if (!selectedId && nextIncidents.incidents?.length) {
        setSelectedId(nextIncidents.incidents[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Nightwatch");
      setLoading(false);
    }
  }, [partitionFilter, selectedId, statusFilter]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    api
      .nightwatchIncident(selectedId)
      .then((data) => {
        if (!cancelled) {
          setDetail(Array.isArray(data?.alerts) ? data : null);
          setDetailLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const partitions = useMemo(
    () => Array.from(new Set(incidents.map((incident) => incident.partition))).sort(),
    [incidents]
  );

  return (
    <div className="h-full overflow-y-auto bg-dark-900 p-4 sm:p-6">
      <div className="mx-auto max-w-7xl space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="s-eyebrow flex items-center gap-2">
              <Moon size={14} /> Nightwatch
            </p>
            <h1 className="s-title">Shadow SRE observer</h1>
            <p className="s-subtitle">
              Observes alerts, investigates with Doctor, recommends actions, and never mutates
              infrastructure.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="s-badge">shadow</span>
            {overview?.enabled ? (
              <span className="s-status s-status-healthy">watching</span>
            ) : (
              <span className="s-status s-status-unknown">unavailable</span>
            )}
            <button type="button" className="s-btn s-btn-secondary" onClick={() => load()}>
              <RefreshCw size={14} /> Refresh
            </button>
          </div>
        </div>

        {overview && !overview.available && (
          <div className="s-card">
            <div className="s-card__body flex items-start gap-3">
              <Eye size={18} className="mt-0.5 text-gray-500" />
              <div>
                <p className="text-sm text-gray-200">Nightwatch is not connected</p>
                <p className="mt-1 text-xs text-gray-500">
                  Set <code>NIGHTWATCH_URL</code> on the agent backend and enable a partition
                  with <code>nightwatch.strata.io/enabled: "true"</code>.
                </p>
                {overview.last_error && (
                  <p className="mt-1 text-xs text-rose-400">{overview.last_error}</p>
                )}
              </div>
            </div>
          </div>
        )}

        {overview && overview.available && (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                { label: "Assignments", value: overview.assignments, icon: Hand },
                { label: "Open incidents", value: overview.open_incidents, icon: AlertTriangle },
                { label: "Investigating", value: overview.investigating, icon: Search },
                { label: "Handed off", value: overview.handed_off, icon: GitBranch },
                { label: "Escalations", value: overview.escalated, icon: ShieldAlert },
              ].map(({ label, value, icon: Icon }) => (
                <div key={label} className="s-card">
                  <div className="s-card__body flex items-center gap-3 py-3">
                    <Icon size={16} className="text-gray-500" />
                    <div>
                      <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
                      <p className="text-lg font-semibold text-gray-100">{value}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="s-card">
              <div className="s-card__header">
                <h2 className="s-card__title">Watched partitions</h2>
                <span className="s-badge">{assignments.filter((item) => item.enabled).length}</span>
              </div>
              <div className="s-card__body flex flex-wrap gap-2">
                {assignments.length === 0 && (
                  <p className="s-empty">
                    No partition has <code>nightwatch.strata.io/enabled: "true"</code>.
                  </p>
                )}
                {assignments.map((assignment) => (
                  <span
                    key={assignment.partition}
                    className={`s-badge ${
                      assignment.enabled ? "text-emerald-300" : "text-gray-600"
                    }`}
                    title={`${assignment.source} · last seen ${assignment.last_seen_at}`}
                  >
                    {assignment.partition}
                    {assignment.enabled ? "" : " (disabled)"}
                  </span>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <select
                className="s-select"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
                aria-label="Filter by status"
              >
                <option value="">All statuses</option>
                <option value="open">Open</option>
                <option value="investigating">Investigating</option>
                <option value="resolved">Resolved</option>
                <option value="failed">Failed</option>
              </select>
              <select
                className="s-select"
                value={partitionFilter}
                onChange={(event) => setPartitionFilter(event.target.value)}
                aria-label="Filter by partition"
              >
                <option value="">All partitions</option>
                {partitions.map((partition) => (
                  <option key={partition} value={partition}>
                    {partition}
                  </option>
                ))}
              </select>
              <span className="s-meta">
                Last poll {overview.last_poll_at ? formatTime(overview.last_poll_at) : "—"}
              </span>
            </div>

            <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
              <div className="s-card min-w-0">
                <div className="s-card__header">
                  <h2 className="s-card__title">Incidents</h2>
                  <span className="s-badge">{incidents.length}</span>
                </div>
                <div className="max-h-[65vh] overflow-y-auto">
                  {loading && <p className="s-empty">Loading incidents…</p>}
                  {!loading && incidents.length === 0 && (
                    <p className="s-empty">No incidents match the current filters.</p>
                  )}
                  {incidents.map((incident) => (
                    <button
                      key={incident.id}
                      type="button"
                      onClick={() => setSelectedId(incident.id)}
                      className={`flex w-full items-start gap-3 border-b border-dark-700 px-3 py-3 text-left transition-colors last:border-b-0 hover:bg-dark-700/50 ${
                        selectedId === incident.id ? "bg-dark-700/70" : ""
                      }`}
                    >
                      <span
                        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                          incident.status === "resolved"
                            ? "bg-emerald-400"
                            : incident.escalation_status !== "none"
                              ? "bg-amber-400"
                              : "bg-blue-400"
                        }`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm text-gray-200">{incident.title}</span>
                          {incident.handoff_status === "handed_off" && (
                            <GitBranch size={12} className="shrink-0 text-amber-400" />
                          )}
                        </span>
                        <span className="mt-0.5 block text-xs text-gray-500">
                          {incident.partition} · {incident.alert_count ?? 0} alert
                          {(incident.alert_count ?? 0) === 1 ? "" : "s"} ·{" "}
                          {formatTime(incident.updated_at)}
                        </span>
                        <span className="mt-1 flex flex-wrap gap-1">
                          <span className={`s-status ${severityClass(incident.severity)}`}>
                            {incident.severity}
                          </span>
                          <span className={`s-status ${statusClass(incident.status)}`}>
                            {incident.status}
                          </span>
                          {incident.escalation_status !== "none" && (
                            <span className="s-badge">escalate</span>
                          )}
                        </span>
                      </span>
                      <ChevronRight size={14} className="mt-1 shrink-0 text-gray-600" />
                    </button>
                  ))}
                </div>
              </div>

              <div className="min-w-0">
                {detailLoading && <p className="s-empty">Loading incident…</p>}
                {!detailLoading && detail && <IncidentDetail incident={detail} />}
                {!detailLoading && !detail && (
                  <div className="s-card">
                    <div className="s-card__body flex flex-col items-center gap-2 py-12 text-center">
                      <Clock size={22} className="text-gray-600" />
                      <p className="text-sm text-gray-400">Select an incident</p>
                      <p className="text-xs text-gray-600">
                        The timeline and evidence appear here.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="s-card">
              <div className="s-card__header">
                <h2 className="s-card__title">Activity</h2>
                <span className="s-badge">{activity.length}</span>
              </div>
              <div className="s-table-wrap">
                <table className="s-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Partition</th>
                      <th>Incident</th>
                      <th>Event</th>
                      <th>Actor</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activity.map((event) => (
                      <tr
                        key={event.id}
                        className="cursor-pointer"
                        onClick={() => setSelectedId(event.incident_id)}
                      >
                        <td className="whitespace-nowrap font-mono text-xs">
                          {formatTime(event.ts)}
                        </td>
                        <td>{event.incident_partition || "—"}</td>
                        <td className="max-w-[240px] truncate">{event.incident_title || "—"}</td>
                        <td className="max-w-[360px] truncate">{event.summary}</td>
                        <td>{event.actor}</td>
                      </tr>
                    ))}
                    {activity.length === 0 && (
                      <tr>
                        <td colSpan={5}>
                          <span className="text-xs text-gray-500">No activity yet.</span>
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {error && (
          <div className="s-card border-rose-500/30">
            <div className="s-card__body text-xs text-rose-400">{error}</div>
          </div>
        )}

        <p className="flex items-center gap-2 text-xs text-gray-600">
          <ArrowUpRight size={12} />
          Shadow mode: Nightwatch recommends only. Assist mode, approvals, and actions are
          intentionally disabled.
        </p>
      </div>
    </div>
  );
}
