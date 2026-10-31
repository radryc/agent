from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.nightwatch.models import (
    AssignmentRow,
    Base,
    EventRow,
    EvidenceRow,
    FindingRow,
    IncidentAlertRow,
    IncidentRow,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()


def _parse_dt(value: datetime | str | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


class NightwatchStore:
    """SQLite-backed incident ledger.

    Shadow mode runs as a single worker, so SQLite is sufficient. The schema
    and event hash chain are deliberately small and can later be mirrored to
    MonoFS without changing the API shape.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._engine = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def init(self) -> None:
        self._engine = create_async_engine(self.database_url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    def _session(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError("NightwatchStore.init() has not been called")
        return self._session_factory()

    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------

    async def upsert_assignment(self, partition: str, enabled: bool, source: str = "guardian-label") -> None:
        now = datetime.utcnow()
        async with self._session() as db:
            row = await db.get(AssignmentRow, partition)
            if row is None:
                db.add(
                    AssignmentRow(
                        partition=partition,
                        enabled=1 if enabled else 0,
                        source=source,
                        discovered_at=now,
                        last_seen_at=now,
                    )
                )
            else:
                row.enabled = 1 if enabled else 0
                row.source = source
                row.last_seen_at = now
            await db.commit()

    async def disable_missing_assignments(self, active_partitions: set[str]) -> None:
        async with self._session() as db:
            result = await db.execute(select(AssignmentRow))
            for row in result.scalars().all():
                if row.partition not in active_partitions and row.enabled:
                    row.enabled = 0
            await db.commit()

    async def list_assignments(self, enabled_only: bool = False) -> list[dict]:
        async with self._session() as db:
            query = select(AssignmentRow).order_by(AssignmentRow.partition)
            if enabled_only:
                query = query.where(AssignmentRow.enabled == 1)
            result = await db.execute(query)
            return [
                {
                    "partition": row.partition,
                    "enabled": bool(row.enabled),
                    "source": row.source,
                    "discovered_at": _iso(row.discovered_at),
                    "last_seen_at": _iso(row.last_seen_at),
                }
                for row in result.scalars().all()
            ]

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    async def get_open_incident(self, correlation_key: str) -> dict | None:
        async with self._session() as db:
            result = await db.execute(
                select(IncidentRow)
                .where(
                    IncidentRow.correlation_key == correlation_key,
                    IncidentRow.status.in_(("open", "investigating")),
                )
                .order_by(IncidentRow.started_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return self._incident_dict(row) if row else None

    async def create_incident(
        self,
        *,
        partition: str,
        correlation_key: str,
        title: str,
        severity: str,
        started_at: datetime | None = None,
        summary: str = "",
    ) -> dict:
        now = datetime.utcnow()
        incident_id = f"inc-{uuid.uuid4().hex}"
        async with self._session() as db:
            row = IncidentRow(
                id=incident_id,
                partition=partition,
                correlation_key=correlation_key,
                title=title,
                status="open",
                severity=severity or "warning",
                started_at=started_at or now,
                updated_at=now,
                summary=summary or None,
            )
            db.add(row)
            await self._append_event(
                db,
                incident_id=incident_id,
                type="incident.opened",
                summary=f"Incident opened for {title}",
                payload={
                    "partition": partition,
                    "correlation_key": correlation_key,
                    "severity": severity,
                    "started_at": _iso(started_at or now),
                },
            )
            await db.commit()
            return self._incident_dict(row)

    async def update_incident(self, incident_id: str, **fields: Any) -> dict | None:
        async with self._session() as db:
            row = await db.get(IncidentRow, incident_id)
            if row is None:
                return None
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = datetime.utcnow()
            await db.commit()
            return self._incident_dict(row)

    async def close_incident(self, incident_id: str, reason: str) -> dict | None:
        now = datetime.utcnow()
        async with self._session() as db:
            row = await db.get(IncidentRow, incident_id)
            if row is None:
                return None
            if row.status == "resolved":
                return self._incident_dict(row)
            row.status = "resolved"
            row.closed_at = now
            row.updated_at = now
            await self._append_event(
                db,
                incident_id=incident_id,
                type="incident.resolved",
                summary=reason or "Incident resolved",
                payload={"resolved_at": _iso(now)},
            )
            await db.commit()
            return self._incident_dict(row)

    async def list_incidents(
        self,
        *,
        status: str | None = None,
        partition: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        async with self._session() as db:
            query = select(IncidentRow).order_by(IncidentRow.updated_at.desc()).limit(max(1, min(limit, 200)))
            if status:
                query = query.where(IncidentRow.status == status)
            if partition:
                query = query.where(IncidentRow.partition == partition)
            result = await db.execute(query)
            rows = result.scalars().all()
            output = []
            for row in rows:
                item = self._incident_dict(row)
                count = await db.execute(
                    select(IncidentAlertRow).where(IncidentAlertRow.incident_id == row.id)
                )
                item["alert_count"] = len(count.scalars().all())
                output.append(item)
            return output

    async def get_incident(self, incident_id: str) -> dict | None:
        async with self._session() as db:
            row = await db.get(IncidentRow, incident_id)
            if row is None:
                return None
            alerts = await db.execute(
                select(IncidentAlertRow)
                .where(IncidentAlertRow.incident_id == incident_id)
                .order_by(IncidentAlertRow.alertname)
            )
            events = await db.execute(
                select(EventRow).where(EventRow.incident_id == incident_id).order_by(EventRow.seq)
            )
            evidence = await db.execute(
                select(EvidenceRow)
                .where(EvidenceRow.incident_id == incident_id)
                .order_by(EvidenceRow.created_at)
            )
            findings = await db.execute(
                select(FindingRow)
                .where(FindingRow.incident_id == incident_id)
                .order_by(FindingRow.created_at)
            )
            item = self._incident_dict(row)
            item["alerts"] = [self._alert_dict(a) for a in alerts.scalars().all()]
            item["events"] = [self._event_dict(e) for e in events.scalars().all()]
            item["evidence"] = [self._evidence_dict(e) for e in evidence.scalars().all()]
            item["findings"] = [self._finding_dict(f) for f in findings.scalars().all()]
            return item

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    async def upsert_alert(self, incident_id: str, alert: dict[str, Any]) -> None:
        fingerprint = str(alert.get("fingerprint") or "")
        if not fingerprint:
            raise ValueError("alert fingerprint is required")
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        async with self._session() as db:
            row = await db.get(IncidentAlertRow, (incident_id, fingerprint))
            values = {
                "alertname": str(alert.get("alertname") or labels.get("alertname") or "alert"),
                "status": str(alert.get("status") or "firing"),
                "severity": str(alert.get("severity") or labels.get("severity") or "warning"),
                "labels_json": _canonical(labels),
                "annotations_json": _canonical(annotations),
                "starts_at": _parse_dt(alert.get("starts_at")),
                "ends_at": _parse_dt(alert.get("ends_at")),
            }
            if row is None:
                db.add(IncidentAlertRow(incident_id=incident_id, fingerprint=fingerprint, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await db.commit()

    async def list_alerts(self, incident_id: str) -> list[dict]:
        async with self._session() as db:
            result = await db.execute(
                select(IncidentAlertRow)
                .where(IncidentAlertRow.incident_id == incident_id)
                .order_by(IncidentAlertRow.alertname)
            )
            return [self._alert_dict(a) for a in result.scalars().all()]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def append_event(
        self,
        *,
        incident_id: str,
        type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        actor: str = "nightwatch",
        ts: datetime | None = None,
    ) -> dict:
        async with self._session() as db:
            row = await self._append_event(
                db,
                incident_id=incident_id,
                type=type,
                summary=summary,
                payload=payload,
                evidence_ids=evidence_ids,
                actor=actor,
                ts=ts,
            )
            await db.commit()
            return self._event_dict(row)

    async def _append_event(
        self,
        db: AsyncSession,
        *,
        incident_id: str,
        type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        actor: str = "nightwatch",
        ts: datetime | None = None,
    ) -> EventRow:
        result = await db.execute(
            select(EventRow)
            .where(EventRow.incident_id == incident_id)
            .order_by(EventRow.seq.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else ""
        event_ts = ts or datetime.utcnow()
        body = {
            "incident_id": incident_id,
            "seq": seq,
            "ts": _iso(event_ts),
            "type": type,
            "actor": actor,
            "summary": summary,
            "payload": payload or {},
            "evidence_ids": evidence_ids or [],
            "prev_hash": prev_hash,
        }
        row = EventRow(
            id=f"evt-{uuid.uuid4().hex}",
            incident_id=incident_id,
            seq=seq,
            ts=event_ts,
            type=type,
            actor=actor,
            summary=summary,
            payload_json=_canonical(payload or {}),
            evidence_ids_json=_canonical(evidence_ids or []),
            prev_hash=prev_hash,
            hash=_event_hash(body),
        )
        db.add(row)
        return row

    async def list_events(self, incident_id: str, limit: int = 200) -> list[dict]:
        async with self._session() as db:
            result = await db.execute(
                select(EventRow)
                .where(EventRow.incident_id == incident_id)
                .order_by(EventRow.seq)
                .limit(max(1, min(limit, 1000)))
            )
            return [self._event_dict(e) for e in result.scalars().all()]

    # ------------------------------------------------------------------
    # Evidence and findings
    # ------------------------------------------------------------------

    async def add_evidence(
        self,
        *,
        incident_id: str,
        kind: str,
        query: str = "",
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        result_summary: str = "",
        result_hash: str = "",
        source: str = "",
        evidence_id: str | None = None,
    ) -> dict:
        row = EvidenceRow(
            id=evidence_id or f"ev-{uuid.uuid4().hex}",
            incident_id=incident_id,
            kind=kind,
            query=query,
            from_ts=from_ts,
            to_ts=to_ts,
            result_summary=result_summary,
            result_hash=result_hash,
            source=source,
        )
        async with self._session() as db:
            db.add(row)
            await db.commit()
            return self._evidence_dict(row)

    async def add_finding(
        self,
        *,
        incident_id: str,
        candidate_partition: str,
        candidate_service: str,
        score: float = 0.0,
        confidence: float = 0.0,
        rationale: str = "",
        handoff_status: str = "none",
    ) -> dict:
        row = FindingRow(
            id=f"find-{uuid.uuid4().hex}",
            incident_id=incident_id,
            candidate_partition=candidate_partition,
            candidate_service=candidate_service,
            score=score,
            confidence=confidence,
            rationale=rationale,
            handoff_status=handoff_status,
        )
        async with self._session() as db:
            db.add(row)
            await db.commit()
            return self._finding_dict(row)

    # ------------------------------------------------------------------
    # Briefs / activity
    # ------------------------------------------------------------------

    async def activity(
        self,
        *,
        since: datetime | None = None,
        partition: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        async with self._session() as db:
            query = (
                select(EventRow, IncidentRow)
                .join(IncidentRow, EventRow.incident_id == IncidentRow.id)
                .order_by(EventRow.ts.desc())
                .limit(max(1, min(limit, 500)))
            )
            if since:
                query = query.where(EventRow.ts >= since)
            if partition:
                query = query.where(IncidentRow.partition == partition)
            result = await db.execute(query)
            return [
                {
                    **self._event_dict(event),
                    "incident_partition": incident.partition,
                    "incident_title": incident.title,
                    "incident_status": incident.status,
                }
                for event, incident in result.all()
            ]

    async def brief(self, since: datetime | None = None, user_subject: str = "") -> dict:
        async with self._session() as db:
            incidents_query = select(IncidentRow).order_by(IncidentRow.updated_at.desc())
            if since:
                incidents_query = incidents_query.where(IncidentRow.updated_at >= since)
            incidents = (await db.execute(incidents_query)).scalars().all()

            events_query = select(EventRow, IncidentRow).join(
                IncidentRow, EventRow.incident_id == IncidentRow.id
            )
            if since:
                events_query = events_query.where(EventRow.ts >= since)
            events_query = events_query.order_by(EventRow.ts.desc()).limit(25)
            events = (await db.execute(events_query)).all()

            counts = {
                "incidents": len(incidents),
                "resolved": sum(1 for i in incidents if i.status == "resolved"),
                "handed_off": sum(1 for i in incidents if i.handoff_status == "handed_off"),
                "escalated": sum(1 for i in incidents if i.escalation_status in {"recommended", "escalated"}),
                "failed": sum(1 for i in incidents if i.status == "failed"),
                "recommendations": sum(1 for i in incidents if i.recommended_action and i.recommended_action != "none"),
            }
            partitions: dict[str, dict] = {}
            for incident in incidents:
                entry = partitions.setdefault(
                    incident.partition,
                    {"partition": incident.partition, "open": 0, "resolved": 0, "handed_off": 0},
                )
                if incident.status == "resolved":
                    entry["resolved"] += 1
                else:
                    entry["open"] += 1
                if incident.handoff_status == "handed_off":
                    entry["handed_off"] += 1

            return {
                "generated_at": _iso(datetime.utcnow()),
                "since": _iso(since),
                "user": user_subject,
                "counts": counts,
                "partitions": sorted(partitions.values(), key=lambda p: p["partition"]),
                "incidents": [self._incident_dict(i) for i in incidents[:20]],
                "notable_events": [
                    {
                        **self._event_dict(event),
                        "incident_partition": incident.partition,
                        "incident_title": incident.title,
                    }
                    for event, incident in events
                ],
            }

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    async def cleanup(self, retention_days: int = 90) -> int:
        cutoff = datetime.utcnow() - timedelta(days=max(1, retention_days))
        async with self._session() as db:
            result = await db.execute(
                select(IncidentRow).where(IncidentRow.updated_at < cutoff)
            )
            rows = result.scalars().all()
            for row in rows:
                await db.execute(delete(EventRow).where(EventRow.incident_id == row.id))
                await db.execute(delete(EvidenceRow).where(EvidenceRow.incident_id == row.id))
                await db.execute(delete(FindingRow).where(FindingRow.incident_id == row.id))
                await db.execute(delete(IncidentAlertRow).where(IncidentAlertRow.incident_id == row.id))
                await db.delete(row)
            await db.commit()
            return len(rows)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _incident_dict(row: IncidentRow) -> dict:
        return {
            "id": row.id,
            "partition": row.partition,
            "correlation_key": row.correlation_key,
            "title": row.title,
            "status": row.status,
            "severity": row.severity,
            "started_at": _iso(row.started_at),
            "updated_at": _iso(row.updated_at),
            "closed_at": _iso(row.closed_at),
            "last_investigated_at": _iso(row.last_investigated_at),
            "root_cause_partition": row.root_cause_partition,
            "root_cause_service": row.root_cause_service,
            "confidence": row.confidence,
            "summary": row.summary,
            "handoff_status": row.handoff_status,
            "escalation_status": row.escalation_status,
            "escalation_target": row.escalation_target,
            "escalation_reason": row.escalation_reason,
            "recommended_action": row.recommended_action,
            "recommendation_target": row.recommendation_target,
            "investigation_error": row.investigation_error,
        }

    @staticmethod
    def _alert_dict(row: IncidentAlertRow) -> dict:
        return {
            "incident_id": row.incident_id,
            "fingerprint": row.fingerprint,
            "alertname": row.alertname,
            "status": row.status,
            "severity": row.severity,
            "labels": _loads(row.labels_json, {}),
            "annotations": _loads(row.annotations_json, {}),
            "starts_at": _iso(row.starts_at),
            "ends_at": _iso(row.ends_at),
        }

    @staticmethod
    def _event_dict(row: EventRow) -> dict:
        return {
            "id": row.id,
            "incident_id": row.incident_id,
            "seq": row.seq,
            "ts": _iso(row.ts),
            "type": row.type,
            "actor": row.actor,
            "summary": row.summary,
            "payload": _loads(row.payload_json, {}),
            "evidence_ids": _loads(row.evidence_ids_json, []),
            "prev_hash": row.prev_hash,
            "hash": row.hash,
        }

    @staticmethod
    def _evidence_dict(row: EvidenceRow) -> dict:
        return {
            "id": row.id,
            "incident_id": row.incident_id,
            "kind": row.kind,
            "query": row.query,
            "from_ts": _iso(row.from_ts),
            "to_ts": _iso(row.to_ts),
            "result_summary": row.result_summary,
            "result_hash": row.result_hash,
            "source": row.source,
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _finding_dict(row: FindingRow) -> dict:
        return {
            "id": row.id,
            "incident_id": row.incident_id,
            "candidate_partition": row.candidate_partition,
            "candidate_service": row.candidate_service,
            "score": row.score,
            "confidence": row.confidence,
            "rationale": row.rationale,
            "handoff_status": row.handoff_status,
            "created_at": _iso(row.created_at),
        }
