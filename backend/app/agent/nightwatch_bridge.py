"""Bridge Nightwatch's evidence pack into the shared SRE flow.

Nightwatch keeps its deterministic pre-collection. This bridge turns the
collected evidence into the same typed reports and Otto synthesis used by the
interactive flow, so both surfaces reason with one contract.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent.contracts import (
    EvidenceRecord,
    IncidentVerdict,
    RootCauseCandidate,
    TelemetryReport,
    TopologyReport,
)
from app.agent.golden_signals import analyze_golden_signals
from app.agent.sre_flow import _synthesize_verdict
from app.config import runtime_bool, settings
from app.nightwatch.llm import assess as legacy_assess

logger = logging.getLogger(__name__)

_KIND_MAP = {
    "metric": "metric",
    "log": "log",
    "trace": "trace",
    "topology": "topology",
    "guardian_event": "topology",
    "code": "code",
}


def _source_for(kind: str) -> str:
    if kind == "topology" or kind == "guardian_event":
        return "guardian"
    if kind == "code":
        return "monofs"
    return "doctor"


def _reports_from_pack(
    pack: dict[str, Any],
) -> tuple[TelemetryReport, TopologyReport, list[RootCauseCandidate]]:
    incident = pack.get("incident") or {}
    partition = str(incident.get("partition") or "")
    telemetry = TelemetryReport(partition=partition)
    topology = TopologyReport(partition=partition)
    for item in pack.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        raw_kind = str(item.get("kind") or "")
        kind = _KIND_MAP.get(raw_kind, "topology")
        summary = str(item.get("summary") or "")
        record = EvidenceRecord(
            kind=kind,  # type: ignore[arg-type]
            source=_source_for(raw_kind),  # type: ignore[arg-type]
            query=str(item.get("query") or ""),
            result_summary=summary,
            empty="empty" in summary.lower(),
        )
        if kind == "metric":
            telemetry.metrics_queried.append(record.query)
        elif kind == "log":
            if any(
                word in summary.lower()
                for word in ("error", "fail", "exception", "timeout", "panic")
            ):
                telemetry.error_logs.append(summary[:240])
        elif kind == "trace":
            if any(
                word in summary.lower()
                for word in ("error", "fail", "exception", "timeout", "panic")
            ):
                telemetry.failing_spans.append(summary[:240])
        else:
            if summary:
                topology.recent_events.append(summary[:240])
        if kind in {"metric", "log", "trace"}:
            telemetry.evidence.append(record)
        else:
            topology.evidence.append(record)

    candidates = []
    for item in pack.get("topology_candidates") or []:
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(
                RootCauseCandidate(
                    partition=str(item.get("partition") or partition),
                    service=str(item.get("service") or "unknown"),
                    score=float(item.get("score") or 0.0),
                    confidence=float(item.get("confidence") or item.get("score") or 0.0),
                    rationale=str(item.get("rationale") or ""),
                )
            )
        except Exception:
            continue
    return telemetry, topology, candidates


def _assessment(verdict: IncidentVerdict, pack: dict[str, Any]) -> dict[str, Any]:
    valid_ids = {
        str(item.get("id"))
        for item in (pack.get("evidence") or [])
        if isinstance(item, dict) and item.get("id")
    }
    cited = [item for item in verdict.evidence_ids if item in valid_ids][:20]
    if not cited:
        verdict.confidence = 0.0
        verdict.escalate = True
        verdict.recommended_action = "escalate"
        reason = verdict.escalation_reason or "no valid evidence citations"
        verdict.escalation_reason = (
            f"{reason}; no valid evidence citations" if "no valid evidence" not in reason else reason
        )
    action = verdict.recommended_action
    if action == "patch_code":
        # Nightwatch shadow mode cannot propose a code change as an action.
        action = "notify"
    return {
        "summary": verdict.summary,
        "root_cause_candidate": {
            "partition": verdict.root_cause_partition,
            "service": verdict.root_cause_service,
            "confidence": verdict.confidence,
        },
        "cross_partition": verdict.cross_partition,
        "recommended_action": action,
        "recommended_target": verdict.recommendation_target or verdict.root_cause_service,
        "rationale": verdict.rationale,
        "evidence_ids": cited,
        "falsification": verdict.falsification,
        "escalate": verdict.escalate,
        "escalation_reason": verdict.escalation_reason,
        "escalation_target": verdict.escalation_target,
        "improvements": [gap.model_dump() for gap in verdict.improvements],
    }


async def assess_with_flow(pack: dict[str, Any]) -> dict[str, Any]:
    """Assess a Nightwatch evidence pack through the shared flow.

    Falls back to the legacy assessor when the flow is disabled or the model
    call fails, so Nightwatch never loses its baseline behaviour.
    """
    if not runtime_bool("agent_flow_enabled", default=True):
        return await legacy_assess(pack)
    incident = pack.get("incident") or {}
    partition = str(incident.get("partition") or "")
    alerts = pack.get("alerts") or []
    alert_names = ", ".join(
        str(item.get("alertname") or "")
        for item in alerts[:5]
        if isinstance(item, dict) and item.get("alertname")
    )
    message = (
        f"Nightwatch shadow investigation. Incident: {incident.get('title') or incident.get('id')}. "
        f"Alerting partition: {partition}. Alerts: {alert_names or 'none'}. "
        "Cite evidence IDs in the rationale. Recommend only actions from the "
        "Nightwatch catalog; never execute anything."
    )
    evidence_ids = [
        str(item.get("id"))
        for item in (pack.get("evidence") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if evidence_ids:
        message += " Available evidence IDs: " + ", ".join(evidence_ids[:20]) + "."
    telemetry, topology, candidates = _reports_from_pack(pack)
    golden_signals = analyze_golden_signals(telemetry, topology)
    try:
        verdict = await _synthesize_verdict(
            message=message,
            partition=partition,
            telemetry=telemetry,
            topology=topology,
            emit=None,
            task_name="outage_otto.nightwatch",
            profile=settings.nightwatch_llm_profile or "local",
            golden_signals=golden_signals,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Flow assessment failed; using legacy assessor: %s", exc)
        return await legacy_assess(pack)
    if candidates and not verdict.candidates:
        verdict.candidates = candidates
    return _assessment(verdict, pack)
