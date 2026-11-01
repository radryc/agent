from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from app.nightwatch.actions import ALLOWED_RECOMMENDATIONS
from app.nightwatch.doctor import DoctorClient
from app.nightwatch.guardian import GuardianClient, NightwatchUpstreamError

logger = logging.getLogger(__name__)

DEFAULT_METRIC_QUERIES = (
    'up{guardian_partition="__PARTITION__"}',
    'guardian_probe_up{guardian_partition="__PARTITION__"}',
    'sum by (service) (rate(process_cpu_seconds_total{guardian_partition="__PARTITION__"}[5m]))',
    'sum by (service) (process_resident_memory_bytes{guardian_partition="__PARTITION__"})',
    'sum by (service, code) (rate(http_requests_total{guardian_partition="__PARTITION__"}[5m]))',
)

@dataclass
class EvidenceDraft:
    kind: str
    query: str
    from_ts: datetime | None
    to_ts: datetime | None
    result_summary: str
    result_hash: str
    source: str


@dataclass
class Candidate:
    partition: str
    service: str
    score: float
    confidence: float
    rationale: str


@dataclass
class InvestigationResult:
    summary: str
    root_cause_partition: str
    root_cause_service: str
    confidence: float
    recommended_action: str
    recommendation_target: str
    rationale: str
    cross_partition: bool
    escalate: bool = False
    escalation_target: str = ""
    escalation_reason: str = ""
    evidence: list[EvidenceDraft] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    error: str = ""


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _prom_summary(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return "no data"
    result = data.get("result")
    if not isinstance(result, list) or not result:
        return "no samples"
    result_type = str(data.get("resultType") or "vector")
    lines: list[str] = []
    for item in result[:5]:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric") or {}
        label_text = ",".join(f"{k}={v}" for k, v in sorted(metric.items()) if k != "__name__")
        if result_type == "matrix":
            values = item.get("values") or []
            if values:
                first = values[0][1] if len(values[0]) > 1 else ""
                last = values[-1][1] if len(values[-1]) > 1 else ""
                lines.append(f"{label_text or 'series'}: {first} -> {last} ({len(values)} points)")
        else:
            value = item.get("value") or []
            sample = value[1] if len(value) > 1 else ""
            lines.append(f"{label_text or 'series'}: {sample}")
    suffix = f" (+{len(result) - 5} more)" if len(result) > 5 else ""
    return f"{len(result)} series; " + "; ".join(lines) + suffix


def _logs_summary(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "no data"
    records = payload.get("logs") or payload.get("entries") or payload.get("records") or payload.get("results")
    if not isinstance(records, list):
        return "no records"
    messages: list[str] = []
    for record in records[:3]:
        if isinstance(record, dict):
            text = record.get("message") or record.get("msg") or record.get("body") or record.get("line")
            if text:
                messages.append(str(text)[:280])
    if not messages:
        return f"{len(records)} records"
    return f"{len(records)} records; " + " | ".join(messages)


def _traces_summary(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "no data"
    traces = payload.get("traces") or payload.get("records") or payload.get("results")
    if not isinstance(traces, list):
        return "no traces"
    services: set[str] = set()
    for trace in traces[:10]:
        if isinstance(trace, dict):
            service = trace.get("service") or trace.get("service_name") or trace.get("rootService")
            if service:
                services.add(str(service))
    suffix = f"; services={', '.join(sorted(services))}" if services else ""
    return f"{len(traces)} traces{suffix}"


def _topology_summary(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "no topology"
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    return f"{len(nodes)} nodes, {len(edges)} edges"


def _history_summary(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "no history"
    events = payload.get("events") or []
    deployments = payload.get("deployments") or []
    parts = []
    for event in events[:5]:
        if isinstance(event, dict):
            parts.append(str(event.get("type") or event.get("title") or "event"))
    return f"{len(events)} events, {len(deployments)} deployments" + (
        f"; recent={', '.join(parts)}" if parts else ""
    )


def _metric_values(payload: dict) -> list[float]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if not isinstance(result, list):
        return []
    values: list[float] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        raw = item.get("value")
        if isinstance(raw, list) and len(raw) > 1:
            try:
                values.append(float(raw[1]))
            except (TypeError, ValueError):
                continue
        elif isinstance(item.get("values"), list):
            for point in item["values"]:
                if isinstance(point, list) and len(point) > 1:
                    try:
                        values.append(float(point[1]))
                    except (TypeError, ValueError):
                        continue
    return values


class Investigator:
    def __init__(
        self,
        guardian: GuardianClient,
        doctor: DoctorClient,
        *,
        assessor=None,
        metric_queries: tuple[str, ...] = DEFAULT_METRIC_QUERIES,
        max_candidates: int = 5,
    ):
        self.guardian = guardian
        self.doctor = doctor
        self.assessor = assessor
        self.metric_queries = metric_queries
        self.max_candidates = max_candidates

    async def investigate(
        self,
        incident: dict,
        alerts: list[dict],
        *,
        evidence_prefix: str = "ev",
    ) -> InvestigationResult:
        partition = str(incident.get("partition") or "")
        now = datetime.utcnow()
        since = now - timedelta(minutes=30)
        evidence: list[EvidenceDraft] = []
        candidates: list[Candidate] = []
        errors: list[str] = []

        topology = await self._collect_topology(partition, evidence, errors)
        await self._collect_history(partition, evidence, errors)
        metrics = await self._collect_metrics(partition, now, evidence, errors)
        await self._collect_logs(partition, since, now, evidence, errors)
        await self._collect_traces(partition, since, now, evidence, errors)

        fleet = await self._fleet_topology(evidence, errors)
        candidates = self._rank_cross_partition_candidates(partition, alerts, fleet)
        metric_candidates = await self._collect_fleet_anomalies(partition, now, evidence, errors)
        candidates = self._merge_candidates(candidates, metric_candidates)
        await self._collect_candidate_metrics(candidates, now, evidence, errors)
        candidates = self._boost_down_candidates(candidates, metrics)

        affected_service = self._affected_service(partition, alerts)
        assessment = await self._assess(
            incident=incident,
            alerts=alerts,
            evidence=evidence,
            candidates=candidates,
            affected_service=affected_service,
            evidence_prefix=evidence_prefix,
        )
        return self._merge_assessment(
            incident=incident,
            assessment=assessment,
            evidence=evidence,
            candidates=candidates,
            affected_service=affected_service,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Evidence collection
    # ------------------------------------------------------------------

    async def _collect_topology(
        self, partition: str, evidence: list[EvidenceDraft], errors: list[str]
    ) -> dict:
        try:
            payload = await self.guardian.topology(partition)
        except NightwatchUpstreamError as exc:
            errors.append(f"guardian topology: {exc}")
            try:
                payload = await self.doctor.guardian_topology(partition)
            except NightwatchUpstreamError as doctor_exc:
                errors.append(f"doctor guardian topology: {doctor_exc}")
                payload = {}
        evidence.append(
            EvidenceDraft(
                kind="topology",
                query=f"guardian topology partition={partition}",
                from_ts=None,
                to_ts=None,
                result_summary=_topology_summary(payload),
                result_hash=_hash(payload),
                source="guardian",
            )
        )
        return payload

    async def _collect_history(
        self, partition: str, evidence: list[EvidenceDraft], errors: list[str]
    ) -> dict:
        try:
            payload = await self.guardian.history(partition, limit=20)
        except NightwatchUpstreamError as exc:
            errors.append(f"guardian history: {exc}")
            try:
                payload = await self.doctor.guardian_events(partition, limit=20)
            except NightwatchUpstreamError as doctor_exc:
                errors.append(f"doctor guardian events: {doctor_exc}")
                payload = {}
        evidence.append(
            EvidenceDraft(
                kind="guardian_event",
                query=f"guardian history partition={partition} limit=20",
                from_ts=None,
                to_ts=None,
                result_summary=_history_summary(payload),
                result_hash=_hash(payload),
                source="guardian",
            )
        )
        return payload

    async def _collect_metrics(
        self,
        partition: str,
        at: datetime,
        evidence: list[EvidenceDraft],
        errors: list[str],
    ) -> list[dict]:
        output: list[dict] = []
        for template in self.metric_queries:
            query = template.replace("__PARTITION__", partition)
            try:
                payload = await self.doctor.metric(query, at=at)
            except NightwatchUpstreamError as exc:
                errors.append(f"metric query failed: {exc}")
                evidence.append(
                    EvidenceDraft(
                        kind="metric",
                        query=query,
                        from_ts=at - timedelta(minutes=5),
                        to_ts=at,
                        result_summary=f"query failed: {exc}",
                        result_hash=_hash({"error": str(exc)}),
                        source="doctor",
                    )
                )
                continue
            output.append({"query": query, "payload": payload})
            evidence.append(
                EvidenceDraft(
                    kind="metric",
                    query=query,
                    from_ts=at - timedelta(minutes=5),
                    to_ts=at,
                    result_summary=_prom_summary(payload),
                    result_hash=_hash(payload),
                    source="doctor",
                )
            )
        return output

    async def _collect_logs(
        self,
        partition: str,
        start: datetime,
        end: datetime,
        evidence: list[EvidenceDraft],
        errors: list[str],
    ) -> None:
        try:
            payload = await self.doctor.logs(partition, query="error", start=start, end=end, limit=20)
            summary = _logs_summary(payload)
        except NightwatchUpstreamError as exc:
            errors.append(f"logs: {exc}")
            summary = f"unavailable: {exc}"
            payload = {"error": str(exc)}
        evidence.append(
            EvidenceDraft(
                kind="log",
                query=f"logs partition={partition} q=error limit=20",
                from_ts=start,
                to_ts=end,
                result_summary=summary,
                result_hash=_hash(payload),
                source="doctor",
            )
        )

    async def _collect_traces(
        self,
        partition: str,
        start: datetime,
        end: datetime,
        evidence: list[EvidenceDraft],
        errors: list[str],
    ) -> None:
        try:
            payload = await self.doctor.recent_traces(partition, start=start, end=end, limit=10)
            summary = _traces_summary(payload)
        except NightwatchUpstreamError as exc:
            errors.append(f"traces: {exc}")
            summary = f"unavailable: {exc}"
            payload = {"error": str(exc)}
        evidence.append(
            EvidenceDraft(
                kind="trace",
                query=f"traces partition={partition} limit=10",
                from_ts=start,
                to_ts=end,
                result_summary=summary,
                result_hash=_hash(payload),
                source="doctor",
            )
        )

    async def _fleet_topology(self, evidence: list[EvidenceDraft], errors: list[str]) -> dict:
        try:
            payload = await self.doctor.guardian_topology(None)
        except NightwatchUpstreamError as exc:
            errors.append(f"fleet topology: {exc}")
            return {}
        evidence.append(
            EvidenceDraft(
                kind="topology",
                query="doctor guardian topology (fleet)",
                from_ts=None,
                to_ts=None,
                result_summary=_topology_summary(payload),
                result_hash=_hash(payload),
                source="doctor",
            )
        )
        return payload

    async def _collect_fleet_anomalies(
        self,
        partition: str,
        at: datetime,
        evidence: list[EvidenceDraft],
        errors: list[str],
    ) -> list[Candidate]:
        """Use fleet-wide metrics to find other partitions with down targets.

        This is deliberately bounded: only ``up == 0`` and probe-down series
        are considered, and the result is capped. It is evidence for a
        cross-partition handoff, not proof of causality.
        """
        queries = (
            f'up{{guardian_partition!="{partition}"}} == 0',
            f'guardian_probe_up{{guardian_partition!="{partition}"}} == 0',
        )
        found: dict[tuple[str, str], Candidate] = {}
        for query in queries:
            try:
                payload = await self.doctor.metric(query, at=at)
            except NightwatchUpstreamError as exc:
                errors.append(f"fleet metric failed: {exc}")
                continue
            evidence.append(
                EvidenceDraft(
                    kind="metric",
                    query=query,
                    from_ts=at - timedelta(minutes=5),
                    to_ts=at,
                    result_summary=_prom_summary(payload),
                    result_hash=_hash(payload),
                    source="doctor",
                )
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, list):
                continue
            for series in result:
                if not isinstance(series, dict):
                    continue
                metric = series.get("metric") or {}
                candidate_partition = str(metric.get("guardian_partition") or "").strip()
                if not candidate_partition or candidate_partition == partition:
                    continue
                service = str(
                    metric.get("service")
                    or metric.get("guardian_service_full")
                    or metric.get("guardian_asset")
                    or candidate_partition
                ).strip()
                key = (candidate_partition, service)
                score = 0.7 if "guardian_probe_up" in query else 0.55
                existing = found.get(key)
                if existing is None or existing.score < score:
                    found[key] = Candidate(
                        partition=candidate_partition,
                        service=service,
                        score=score,
                        confidence=0.35,
                        rationale=f"fleet metric indicates a down target ({query})",
                    )
        return sorted(found.values(), key=lambda item: item.score, reverse=True)[: self.max_candidates]

    @staticmethod
    def _merge_candidates(
        primary: list[Candidate],
        extra: list[Candidate],
    ) -> list[Candidate]:
        merged: dict[tuple[str, str], Candidate] = {
            (item.partition, item.service): item for item in primary
        }
        for item in extra:
            key = (item.partition, item.service)
            if key not in merged or merged[key].score < item.score:
                merged[key] = item
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:10]

    # ------------------------------------------------------------------
    # Candidate ranking
    # ------------------------------------------------------------------

    @staticmethod
    def _affected_service(partition: str, alerts: list[dict]) -> str:
        for alert in alerts:
            service = str(alert.get("service") or "").strip()
            if service:
                return service
            labels = alert.get("labels") or {}
            asset = labels.get("guardian_asset")
            if asset:
                return f"{partition}.{asset}"
        return partition

    def _rank_cross_partition_candidates(
        self,
        partition: str,
        alerts: list[dict],
        fleet: dict,
    ) -> list[Candidate]:
        if not isinstance(fleet, dict):
            fleet = {}
        nodes = fleet.get("nodes") or []
        edges = fleet.get("edges") or []
        if not isinstance(nodes, list):
            nodes = []
        if not isinstance(edges, list):
            edges = []

        affected = self._affected_service(partition, alerts)
        candidates: list[Candidate] = []
        seen_candidates: set[tuple[str, str]] = set()

        # Alert labels are the cheapest and often strongest cross-partition
        # signal: probes and flow metrics can identify the target component
        # directly in guardian_target_* labels.
        for alert in alerts:
            target_partition = str(alert.get("target_partition") or "").strip()
            target_service = str(alert.get("target_service") or "").strip()
            if not target_partition or target_partition == partition:
                continue
            key = (target_partition, target_service or target_partition)
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidates.append(
                Candidate(
                    partition=target_partition,
                    service=target_service or target_partition,
                    score=0.85,
                    confidence=0.6,
                    rationale="alert carries guardian_target_* labels for another partition",
                )
            )

        seeds: set[str] = set()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_partition = str(node.get("partition") or "")
            name = str(node.get("name") or node.get("asset") or "")
            if node_partition != partition:
                continue
            if not affected or affected == partition or affected.endswith(name) or name in affected:
                seeds.add(str(node.get("id") or name))
        if not seeds:
            return []

        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("from") or "")
            dst = str(edge.get("to") or "")
            if src and dst:
                adjacency.setdefault(src, set()).add(dst)
                adjacency.setdefault(dst, set()).add(src)

        by_id = {
            str(node.get("id") or node.get("name")): node
            for node in nodes
            if isinstance(node, dict)
        }
        seen: set[str] = set(seeds)
        frontier = set(seeds)
        for depth in (1, 2):
            next_frontier: set[str] = set()
            for node_id in frontier:
                for neighbor in adjacency.get(node_id, set()):
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    next_frontier.add(neighbor)
                    node = by_id.get(neighbor)
                    if not node:
                        continue
                    node_partition = str(node.get("partition") or "")
                    if not node_partition or node_partition == partition:
                        continue
                    service = str(node.get("asset") or node.get("name") or neighbor)
                    key = (node_partition, service)
                    if key in seen_candidates:
                        continue
                    seen_candidates.add(key)
                    health = str(node.get("health") or node.get("status") or "").lower()
                    score = 0.6 if depth == 1 else 0.4
                    if health in {"critical", "failing", "unhealthy", "down"}:
                        score += 0.2
                    candidates.append(
                        Candidate(
                            partition=node_partition,
                            service=service,
                            score=min(1.0, score),
                            confidence=0.0,
                            rationale=f"topology distance {depth} from {affected or partition}",
                        )
                    )
            frontier = next_frontier
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[: self.max_candidates]

    async def _collect_candidate_metrics(
        self,
        candidates: list[Candidate],
        at: datetime,
        evidence: list[EvidenceDraft],
        errors: list[str],
    ) -> None:
        for candidate in candidates:
            query = f'up{{guardian_partition="{candidate.partition}"}}'
            try:
                payload = await self.doctor.metric(query, at=at)
            except NightwatchUpstreamError as exc:
                errors.append(f"candidate metric failed: {exc}")
                continue
            values = _metric_values(payload)
            if values and min(values) == 0:
                candidate.score = min(1.0, candidate.score + 0.2)
                candidate.rationale += "; candidate partition has down targets"
            evidence.append(
                EvidenceDraft(
                    kind="metric",
                    query=query,
                    from_ts=at - timedelta(minutes=5),
                    to_ts=at,
                    result_summary=_prom_summary(payload),
                    result_hash=_hash(payload),
                    source="doctor",
                )
            )

    @staticmethod
    def _boost_down_candidates(candidates: list[Candidate], metrics: list[dict]) -> list[Candidate]:
        if not candidates:
            return candidates
        probe_down = False
        up_down = False
        for item in metrics:
            query = str(item.get("query") or "")
            values = _metric_values(item.get("payload") or {})
            if "guardian_probe_up" in query and values and min(values) == 0:
                probe_down = True
            if query.startswith("up{") and values and min(values) == 0:
                up_down = True
        if probe_down or up_down:
            for candidate in candidates:
                candidate.score = min(1.0, candidate.score + 0.1)
        return candidates

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------

    async def _assess(
        self,
        *,
        incident: dict,
        alerts: list[dict],
        evidence: list[EvidenceDraft],
        candidates: list[Candidate],
        affected_service: str,
        evidence_prefix: str,
    ) -> dict:
        if self.assessor is None:
            return {}
        pack = {
            "incident": {
                "id": incident.get("id"),
                "partition": incident.get("partition"),
                "title": incident.get("title"),
                "severity": incident.get("severity"),
            },
            "alerts": [
                {
                    "alertname": alert.get("alertname"),
                    "severity": alert.get("severity"),
                    "labels": alert.get("labels"),
                    "annotations": alert.get("annotations"),
                }
                for alert in alerts[:10]
            ],
            "affected_service": affected_service,
            "topology_candidates": [candidate.__dict__ for candidate in candidates],
            "evidence": [
                {
                    "id": f"{evidence_prefix}-{index}",
                    "kind": item.kind,
                    "query": item.query,
                    "summary": item.result_summary,
                }
                for index, item in enumerate(evidence)
            ],
        }
        try:
            result = await self.assessor(pack)
            return result if isinstance(result, dict) else {}
        except Exception as exc:  # pragma: no cover - network/model failure path
            logger.warning("Nightwatch assessment failed: %s", exc)
            return {"error": str(exc)}

    def _merge_assessment(
        self,
        *,
        incident: dict,
        assessment: dict,
        evidence: list[EvidenceDraft],
        candidates: list[Candidate],
        affected_service: str,
        errors: list[str],
    ) -> InvestigationResult:
        partition = str(incident.get("partition") or "")
        top = candidates[0] if candidates else None
        candidate = assessment.get("root_cause_candidate") or {}
        if not isinstance(candidate, dict):
            candidate = {}
        root_partition = str(candidate.get("partition") or (top.partition if top else partition))
        root_service = str(candidate.get("service") or (top.service if top else affected_service))
        try:
            confidence = float(candidate.get("confidence") or (top.score if top else 0.2))
        except (TypeError, ValueError):
            confidence = 0.2
        confidence = max(0.0, min(1.0, confidence))

        recommended_action = str(assessment.get("recommended_action") or "").strip().lower()
        if recommended_action not in ALLOWED_RECOMMENDATIONS:
            recommended_action = self._deterministic_recommendation(evidence)
        target = str(assessment.get("recommended_target") or root_service or "")
        cross_partition = bool(root_partition and root_partition != partition)
        if cross_partition:
            recommended_action = "handoff"
            target = root_service

        summary = str(assessment.get("summary") or "").strip()
        if not summary:
            summary = self._deterministic_summary(
                partition=partition,
                affected_service=affected_service,
                candidate_partition=root_partition,
                candidate_service=root_service,
                confidence=confidence,
                evidence_count=len(evidence),
                cross_partition=cross_partition,
            )
        rationale = str(assessment.get("rationale") or assessment.get("falsification") or "").strip()
        if not rationale and top:
            rationale = top.rationale

        escalation_reasons: list[str] = []
        if cross_partition:
            escalation_reasons.append("root-cause candidate is outside the assigned partition")
        if confidence < settings.nightwatch_escalation_confidence:
            escalation_reasons.append(
                f"confidence {confidence:.2f} is below escalation threshold "
                f"{settings.nightwatch_escalation_confidence:.2f}"
            )
        if recommended_action in {"none", "escalate", "handoff"}:
            escalation_reasons.append("no safe, verifiable action is available")
        if errors and len(evidence) <= 2:
            escalation_reasons.append("investigation could not collect enough evidence")
        llm_reason = str(assessment.get("escalation_reason") or "").strip()
        if llm_reason:
            escalation_reasons.append(llm_reason)
        escalate = bool(assessment.get("escalate")) or bool(escalation_reasons)
        escalation_target = str(
            assessment.get("escalation_target")
            or (root_partition if cross_partition else partition)
            or partition
        ).strip()
        escalation_reason = "; ".join(dict.fromkeys(escalation_reasons))
        if escalate and not cross_partition:
            # Never recommend a mutating action when confidence is low or the
            # evidence is insufficient. Escalate instead.
            recommended_action = "escalate"
            target = partition

        return InvestigationResult(
            summary=summary,
            root_cause_partition=root_partition,
            root_cause_service=root_service,
            confidence=confidence,
            recommended_action=recommended_action,
            recommendation_target=target,
            rationale=rationale,
            cross_partition=cross_partition,
            escalate=escalate,
            escalation_target=escalation_target,
            escalation_reason=escalation_reason,
            evidence=evidence,
            candidates=candidates,
            error="; ".join(errors[-5:]),
        )

    @staticmethod
    def _deterministic_recommendation(evidence: list[EvidenceDraft]) -> str:
        text = " ".join(
            f"{item.query} {item.result_summary}" for item in evidence if item.kind == "metric"
        ).lower()
        if "guardian_probe_up" in text and (" 0" in text or ": 0" in text):
            return "reconcile"
        if "up{" in text and (" 0" in text or ": 0" in text):
            return "restart"
        return "none"

    @staticmethod
    def _deterministic_summary(
        *,
        partition: str,
        affected_service: str,
        candidate_partition: str,
        candidate_service: str,
        confidence: float,
        evidence_count: int,
        cross_partition: bool,
    ) -> str:
        if cross_partition:
            return (
                f"{affected_service or partition} is alerting. Shadow analysis points to "
                f"{candidate_service or candidate_partition} in partition "
                f"{candidate_partition} (confidence {confidence:.2f}); handoff only, no action taken."
            )
        return (
            f"{affected_service or partition} is alerting. Shadow analysis collected "
            f"{evidence_count} evidence items and selected {candidate_service or partition} "
            f"as the best local candidate (confidence {confidence:.2f}). No action taken."
        )
