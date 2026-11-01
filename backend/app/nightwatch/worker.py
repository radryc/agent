from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.nightwatch.doctor import DoctorClient
from app.nightwatch.guardian import GuardianClient, enabled_partitions
from app.nightwatch.investigator import Investigator
from app.nightwatch.sentinel import correlation_key, normalize_alerts
from app.nightwatch.state import state
from app.nightwatch.store import NightwatchStore

logger = logging.getLogger(__name__)

INVESTIGATION_COOLDOWN = timedelta(minutes=5)


class NightwatchWorker:
    """Shadow-mode alert observer.

    The worker never mutates Guardian, Doctor, or Sentinel. It only reads and
    records incidents/evidence/findings in its own store.
    """

    def __init__(
        self,
        store: NightwatchStore,
        guardian: GuardianClient,
        doctor: DoctorClient,
        investigator: Investigator,
        *,
        assignment_label: str | None = None,
        tenant: str | None = None,
        correlation_window_minutes: int | None = None,
        max_concurrent_investigations: int | None = None,
    ):
        self.store = store
        self.guardian = guardian
        self.doctor = doctor
        self.investigator = investigator
        self.assignment_label = assignment_label or settings.nightwatch_assignment_label
        self.tenant = tenant or settings.nightwatch_tenant
        self.correlation_window = timedelta(
            minutes=correlation_window_minutes
            if correlation_window_minutes is not None
            else settings.nightwatch_correlation_window_minutes
        )
        self.max_concurrent = max(
            1,
            max_concurrent_investigations
            if max_concurrent_investigations is not None
            else settings.nightwatch_max_concurrent_investigations,
        )
        self._last_cleanup: datetime | None = None

    async def sync_assignments(self) -> list[dict]:
        partitions = await self.guardian.list_partitions()
        enabled = enabled_partitions(partitions, self.assignment_label)
        names = {
            str(item.get("name") or "")
            for item in enabled
            if isinstance(item, dict) and item.get("name")
        }
        for name in sorted(names):
            await self.store.upsert_assignment(name, True, source="guardian-label")
        await self.store.disable_missing_assignments(names)
        return await self.store.list_assignments(enabled_only=True)

    async def run_once(self) -> None:
        state.last_error = ""
        try:
            assignments = await self.sync_assignments()
            if not assignments:
                logger.info("Nightwatch: no enabled assignments")
                state.last_poll_at = datetime.utcnow()
                state.iterations += 1
                return

            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def bounded(partition: str) -> None:
                async with semaphore:
                    try:
                        await self.poll_partition(partition)
                    except Exception as exc:
                        logger.exception("Nightwatch: partition %s poll failed", partition)
                        state.last_error = f"{partition}: {exc}"

            await asyncio.gather(*(bounded(item["partition"]) for item in assignments))
            now = datetime.utcnow()
            if self._last_cleanup is None or now - self._last_cleanup >= timedelta(hours=1):
                removed = await self.store.cleanup(settings.nightwatch_retention_days)
                if removed:
                    logger.info("Nightwatch retention removed %s old incidents", removed)
                self._last_cleanup = now
            state.last_poll_at = now
            state.iterations += 1
        except Exception as exc:
            state.last_error = str(exc)
            state.last_poll_at = datetime.utcnow()
            raise

    async def poll_partition(self, partition: str) -> None:
        raw_alerts = await self.doctor.alerts(partition)
        alerts = normalize_alerts(
            raw_alerts,
            default_partition=partition,
            default_tenant=self.tenant,
        )
        if not alerts:
            await self._close_resolved_incidents(partition)
            return

        for alert in alerts:
            key = correlation_key(alert)
            incident = await self.store.get_open_incident(key)
            created_new = incident is None
            existing_fingerprints: set[str] = set()
            if incident is None:
                if str(alert.get("status")) not in {"firing", "pending"}:
                    continue
                incident = await self.store.create_incident(
                    partition=partition,
                    correlation_key=key,
                    title=self._title(alert),
                    severity=str(alert.get("severity") or "warning"),
                    started_at=alert.get("starts_at"),
                    summary=self._summary(alert),
                )
                await self.store.append_event(
                    incident_id=incident["id"],
                    type="alert.firing",
                    summary=self._summary(alert),
                    payload={
                        "alertname": alert.get("alertname"),
                        "fingerprint": alert.get("fingerprint"),
                        "severity": alert.get("severity"),
                        "labels": alert.get("labels"),
                    },
                )
            else:
                existing_fingerprints = {
                    item["fingerprint"] for item in await self.store.list_alerts(incident["id"])
                }
            await self.store.upsert_alert(incident["id"], alert)
            if (
                not created_new
                and incident is not None
                and alert.get("fingerprint")
                and alert["fingerprint"] not in existing_fingerprints
                and str(alert.get("status")) in {"firing", "pending"}
            ):
                await self.store.append_event(
                    incident_id=incident["id"],
                    type="alert.attached",
                    summary=f"Attached alert {alert.get('alertname')} to the incident.",
                    payload={
                        "alertname": alert.get("alertname"),
                        "fingerprint": alert.get("fingerprint"),
                        "severity": alert.get("severity"),
                    },
                )

            if str(alert.get("status")) in {"firing", "pending"}:
                await self._maybe_investigate(incident["id"])

        await self._close_resolved_incidents(partition)
        await self._escalate_stale(partition)

    async def _escalate_stale(self, partition: str) -> None:
        threshold = timedelta(minutes=max(1, settings.nightwatch_escalation_after_minutes))
        now = datetime.utcnow()
        incidents = await self.store.list_incidents(status=None, partition=partition, limit=200)
        for incident in incidents:
            if incident["status"] in {"resolved", "failed"}:
                continue
            if incident.get("escalation_status") not in {None, "none"}:
                continue
            started = incident.get("started_at")
            if not started:
                continue
            try:
                started_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                if started_dt.tzinfo is not None:
                    started_dt = started_dt.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                continue
            if now - started_dt < threshold:
                continue
            reason = (
                f"incident has remained open for more than "
                f"{settings.nightwatch_escalation_after_minutes} minutes without a confident resolution"
            )
            await self.store.update_incident(
                incident["id"],
                escalation_status="recommended",
                escalation_target=partition,
                escalation_reason=reason,
            )
            await self.store.append_event(
                incident_id=incident["id"],
                type="escalation.recommended",
                summary=f"Escalation recommended to {partition}.",
                payload={
                    "target": partition,
                    "reason": reason,
                    "confidence": incident.get("confidence"),
                },
            )

    async def _maybe_investigate(self, incident_id: str) -> None:
        incident = await self.store.get_incident(incident_id)
        if not incident:
            return
        if incident["status"] not in {"open", "investigating"}:
            return
        last = incident.get("last_investigated_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if last_dt.tzinfo is not None:
                    last_dt = last_dt.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                last_dt = None
            if last_dt and datetime.utcnow() - last_dt < INVESTIGATION_COOLDOWN:
                return

        alerts = incident.get("alerts") or []
        evidence_prefix = f"ev-{incident_id}-{uuid.uuid4().hex[:8]}"
        await self.store.update_incident(incident_id, status="investigating")
        await self.store.append_event(
            incident_id=incident_id,
            type="investigation.started",
            summary=f"Shadow investigation started for {incident['title']}",
            payload={"alert_count": len(alerts)},
        )
        try:
            result = await self.investigator.investigate(
                incident, alerts, evidence_prefix=evidence_prefix
            )
        except Exception as exc:
            logger.exception("Nightwatch: investigation failed for %s", incident_id)
            await self.store.update_incident(
                incident_id,
                status="open",
                last_investigated_at=datetime.utcnow(),
                investigation_error=str(exc),
                escalation_status="recommended",
                escalation_target=incident["partition"],
                escalation_reason=f"shadow investigation failed: {exc}",
            )
            await self.store.append_event(
                incident_id=incident_id,
                type="investigation.failed",
                summary=f"Shadow investigation failed: {exc}",
                payload={"error": str(exc)},
            )
            if incident.get("escalation_status") in {None, "none"}:
                await self.store.append_event(
                    incident_id=incident_id,
                    type="escalation.recommended",
                    summary="Escalation recommended because the shadow investigation failed.",
                    payload={
                        "target": incident["partition"],
                        "reason": f"shadow investigation failed: {exc}",
                    },
                )
            return

        evidence_ids: list[str] = []
        for index, draft in enumerate(result.evidence):
            evidence_id = f"{evidence_prefix}-{index}"
            record = await self.store.add_evidence(
                incident_id=incident_id,
                evidence_id=evidence_id,
                kind=draft.kind,
                query=draft.query,
                from_ts=draft.from_ts,
                to_ts=draft.to_ts,
                result_summary=draft.result_summary,
                result_hash=draft.result_hash,
                source=draft.source,
            )
            evidence_ids.append(record["id"])

        for candidate in result.candidates:
            await self.store.add_finding(
                incident_id=incident_id,
                candidate_partition=candidate.partition,
                candidate_service=candidate.service,
                score=candidate.score,
                confidence=candidate.confidence,
                rationale=candidate.rationale,
                handoff_status="handed_off" if result.cross_partition else "none",
            )

        handoff_status = "handed_off" if result.cross_partition else "none"
        escalation_status = "recommended" if result.escalate else "none"
        await self.store.update_incident(
            incident_id,
            status="open",
            last_investigated_at=datetime.utcnow(),
            root_cause_partition=result.root_cause_partition,
            root_cause_service=result.root_cause_service,
            confidence=result.confidence,
            summary=result.summary,
            handoff_status=handoff_status,
            escalation_status=escalation_status,
            escalation_target=result.escalation_target or None,
            escalation_reason=result.escalation_reason or None,
            recommended_action=result.recommended_action,
            recommendation_target=result.recommendation_target,
            investigation_error=result.error or None,
        )
        await self.store.append_event(
            incident_id=incident_id,
            type="investigation.completed",
            summary=result.summary,
            payload={
                "root_cause_partition": result.root_cause_partition,
                "root_cause_service": result.root_cause_service,
                "confidence": result.confidence,
                "recommended_action": result.recommended_action,
                "recommendation_target": result.recommendation_target,
                "rationale": result.rationale,
                "candidates": [candidate.__dict__ for candidate in result.candidates],
                "errors": result.error,
            },
            evidence_ids=evidence_ids,
        )
        if result.cross_partition:
            await self.store.append_event(
                incident_id=incident_id,
                type="finding.cross_partition",
                summary=(
                    f"Root-cause candidate {result.root_cause_service} is outside "
                    f"{incident['partition']}; handoff only."
                ),
                payload={
                    "candidate_partition": result.root_cause_partition,
                    "candidate_service": result.root_cause_service,
                    "confidence": result.confidence,
                },
                evidence_ids=evidence_ids,
            )
        else:
            await self.store.append_event(
                incident_id=incident_id,
                type="finding.local",
                summary=(
                    f"Root-cause candidate {result.root_cause_service} is inside "
                    f"{incident['partition']}."
                ),
                payload={
                    "candidate_partition": result.root_cause_partition,
                    "candidate_service": result.root_cause_service,
                    "confidence": result.confidence,
                },
                evidence_ids=evidence_ids,
            )
        if result.escalate:
            await self.store.append_event(
                incident_id=incident_id,
                type="escalation.recommended",
                summary=f"Escalation recommended to {result.escalation_target}.",
                payload={
                    "target": result.escalation_target,
                    "reason": result.escalation_reason,
                    "confidence": result.confidence,
                    "recommended_action": result.recommended_action,
                    "cross_partition": result.cross_partition,
                },
                evidence_ids=evidence_ids,
            )

    async def _close_resolved_incidents(self, partition: str) -> None:
        incidents = await self.store.list_incidents(
            status=None, partition=partition, limit=200
        )
        for incident in incidents:
            if incident["status"] == "resolved":
                continue
            alerts = await self.store.list_alerts(incident["id"])
            if not alerts:
                continue
            if all(str(alert.get("status")) == "resolved" for alert in alerts):
                await self.store.close_incident(
                    incident["id"],
                    reason="All attached alerts are resolved.",
                )

    @staticmethod
    def _title(alert: dict) -> str:
        service = str(alert.get("service") or "").strip()
        alertname = str(alert.get("alertname") or "alert")
        return f"{alertname} · {service}" if service else alertname

    @staticmethod
    def _summary(alert: dict) -> str:
        annotations = alert.get("annotations") or {}
        summary = annotations.get("summary") or annotations.get("description")
        if summary:
            return str(summary)
        service = str(alert.get("service") or alert.get("partition") or "")
        return f"{alert.get('alertname') or 'alert'} on {service}".strip()


async def worker_loop(worker: NightwatchWorker) -> None:
    interval = max(5, int(settings.nightwatch_poll_interval_seconds))
    logger.info("Nightwatch shadow worker started (poll=%ss)", interval)
    while True:
        try:
            await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Nightwatch worker iteration failed")
        await asyncio.sleep(interval)
