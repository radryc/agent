"""Typed contracts shared by the SRE flow, Nightwatch, and the UI.

Keeping these models in one module prevents the interactive agent, the
background investigator, and the WebSocket event layer from drifting apart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceKind = Literal["metric", "log", "trace", "topology", "code"]
EvidenceSource = Literal["doctor", "guardian", "monofs"]
RecommendedAction = Literal[
    "none",
    "notify",
    "ack",
    "silence",
    "escalate",
    "handoff",
    "reconcile",
    "restart",
    "scale",
    "rollback",
    "patch_code",
]


class EvidenceRecord(BaseModel):
    kind: EvidenceKind
    source: EvidenceSource
    query: str
    result_summary: str
    raw_hash: str = ""
    empty: bool = False


class ObservabilityGap(BaseModel):
    title: str
    category: Literal[
        "metrics", "logs", "traces", "labels", "alerting", "dashboard", "docs"
    ]
    rationale: str
    proposed_change: str
    target: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class TelemetryReport(BaseModel):
    partition: str
    metrics_queried: list[str] = Field(default_factory=list)
    anomalous_metrics: list[str] = Field(default_factory=list)
    error_logs: list[str] = Field(default_factory=list)
    failing_spans: list[str] = Field(default_factory=list)
    empty_signals: list[str] = Field(default_factory=list)
    observability_gaps: list[ObservabilityGap] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class TopologyReport(BaseModel):
    partition: str
    health_status: str = "unknown"
    drifted_intents: list[str] = Field(default_factory=list)
    failing_assets: list[str] = Field(default_factory=list)
    recent_events: list[str] = Field(default_factory=list)
    cross_partition_targets: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class RootCauseCandidate(BaseModel):
    partition: str
    service: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str


class GoldenSignalCoverage(BaseModel):
    signal: Literal["latency", "traffic", "errors", "saturation"]
    covered: bool
    evidence: list[str] = Field(default_factory=list)
    gap: str = ""
    recommended_query: str = ""


class IncidentVerdict(BaseModel):
    summary: str
    root_cause_partition: str
    root_cause_service: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    cross_partition: bool = False
    escalate: bool = False
    escalation_target: str = ""
    escalation_reason: str = ""
    recommended_action: RecommendedAction = "none"
    recommendation_target: str = ""
    rationale: str = ""
    falsification: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    candidates: list[RootCauseCandidate] = Field(default_factory=list)
    improvements: list[ObservabilityGap] = Field(default_factory=list)
    golden_signals: list[GoldenSignalCoverage] = Field(default_factory=list)
    otto_response: str = Field(
        default="",
        description="Final response written in OutageOtto's dry, technical SRE voice.",
    )


class SteveProposalReport(BaseModel):
    files_changed: list[str] = Field(default_factory=list)
    unified_diff: str = ""
    validation_result: str = ""
    verification_command: str = ""
    summary: str = ""


# ---------------------------------------------------------------------------
# WebSocket event payloads
# ---------------------------------------------------------------------------


class CrewStepEvent(BaseModel):
    type: Literal["crew_step"] = "crew_step"
    agent_id: Literal[
        "telemetry_hound", "topology_auditor", "software_steve", "outage_otto"
    ]
    agent_name: str
    task_name: str
    status: Literal["started", "tool_call", "tool_result", "completed", "failed"]
    tool: str | None = None
    tool_args: dict | None = None
    summary: str | None = None
    ts: str


class ApprovalRequestEvent(BaseModel):
    type: Literal["approval_required"] = "approval_required"
    id: str
    tool: Literal[
        "guardian_reconcile",
        "guardian_deploy_partition",
        "workspace_commit",
        "workspace_push",
    ]
    args: dict = Field(default_factory=dict)
    rationale: str
    diff: str | None = None
