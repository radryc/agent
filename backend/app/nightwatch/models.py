from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AssignmentRow(Base):
    __tablename__ = "assignments"

    partition: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String, default="guardian-label", nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    partition: Mapped[str] = mapped_column(String, index=True, nullable=False)
    correlation_key: Mapped[str] = mapped_column(String, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, index=True, default="open", nullable=False)
    severity: Mapped[str] = mapped_column(String, default="warning", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_investigated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    root_cause_partition: Mapped[str | None] = mapped_column(String, nullable=True)
    root_cause_service: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_status: Mapped[str] = mapped_column(String, default="none", nullable=False)
    escalation_status: Mapped[str] = mapped_column(String, default="none", nullable=False)
    escalation_target: Mapped[str | None] = mapped_column(String, nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation_target: Mapped[str | None] = mapped_column(String, nullable=True)
    investigation_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IncidentAlertRow(Base):
    __tablename__ = "incident_alerts"

    incident_id: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, primary_key=True)
    alertname: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, default="warning", nullable=False)
    labels_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    annotations_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("incident_id", "seq", name="uq_events_incident_seq"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, default="nightwatch", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    prev_hash: Mapped[str] = mapped_column(String, default="", nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    query: Mapped[str] = mapped_column(Text, default="", nullable=False)
    from_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    to_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result_hash: Mapped[str] = mapped_column(String, default="", nullable=False)
    source: Mapped[str] = mapped_column(String, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    candidate_partition: Mapped[str] = mapped_column(String, nullable=False)
    candidate_service: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    handoff_status: Mapped[str] = mapped_column(String, default="none", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BriefRow(Base):
    __tablename__ = "briefs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_subject: Mapped[str] = mapped_column(String, index=True, default="", nullable=False)
    since_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
