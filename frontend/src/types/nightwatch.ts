export interface NightwatchOverview {
  available: boolean;
  mode: string;
  enabled: boolean;
  assignments: number;
  open_incidents: number;
  investigating: number;
  handed_off: number;
  escalated: number;
  resolved: number;
  last_poll_at: string | null;
  last_error: string;
}

export interface NightwatchIncident {
  id: string;
  partition: string;
  correlation_key: string;
  title: string;
  status: string;
  severity: string;
  started_at: string;
  updated_at: string;
  closed_at: string | null;
  last_investigated_at: string | null;
  root_cause_partition: string | null;
  root_cause_service: string | null;
  confidence: number | null;
  summary: string | null;
  handoff_status: string;
  escalation_status: string;
  escalation_target: string | null;
  escalation_reason: string | null;
  recommended_action: string | null;
  recommendation_target: string | null;
  investigation_error: string | null;
  alert_count?: number;
}

export interface NightwatchAlert {
  incident_id: string;
  fingerprint: string;
  alertname: string;
  status: string;
  severity: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  starts_at: string | null;
  ends_at: string | null;
}

export interface NightwatchEvent {
  id: string;
  incident_id: string;
  seq: number;
  ts: string;
  type: string;
  actor: string;
  summary: string;
  payload: Record<string, unknown>;
  evidence_ids: string[];
  prev_hash: string;
  hash: string;
  incident_partition?: string;
  incident_title?: string;
  incident_status?: string;
}

export interface NightwatchEvidence {
  id: string;
  incident_id: string;
  kind: string;
  query: string;
  from_ts: string | null;
  to_ts: string | null;
  result_summary: string;
  result_hash: string;
  source: string;
  created_at: string;
}

export interface NightwatchFinding {
  id: string;
  incident_id: string;
  candidate_partition: string;
  candidate_service: string;
  score: number;
  confidence: number;
  rationale: string;
  handoff_status: string;
  created_at: string;
}

export interface NightwatchIncidentDetail extends NightwatchIncident {
  alerts: NightwatchAlert[];
  events: NightwatchEvent[];
  evidence: NightwatchEvidence[];
  findings: NightwatchFinding[];
}

export interface NightwatchBrief {
  available: boolean;
  generated_at: string | null;
  since: string | null;
  counts: {
    incidents: number;
    resolved: number;
    handed_off: number;
    escalated: number;
    failed: number;
    recommendations: number;
  };
  partitions: Array<{
    partition: string;
    open: number;
    resolved: number;
    handed_off: number;
  }>;
  incidents: NightwatchIncident[];
  notable_events: NightwatchEvent[];
}

export interface NightwatchAssignment {
  partition: string;
  enabled: boolean;
  source: string;
  discovered_at: string;
  last_seen_at: string;
}

export interface NightwatchIncidentsResponse {
  available: boolean;
  incidents: NightwatchIncident[];
}

export interface NightwatchActivityResponse {
  available: boolean;
  activity: NightwatchEvent[];
}
