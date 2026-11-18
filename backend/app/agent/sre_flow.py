"""Deterministic CrewAI-style flow for Strata.

``StrataSREFlow`` classifies a turn into one of a small number of routes and
runs narrowly-scoped specialists. It is intentionally dependency-free: the
orchestration primitives are ordinary async Python, which keeps the backend
buildable in an air-gapped container.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from app.agent import loop as loop_mod
from app.agent import session as session_mgr
from app.agent.contracts import (
    GoldenSignalCoverage,
    IncidentVerdict,
    ObservabilityGap,
    RootCauseCandidate,
    SteveProposalReport,
    TelemetryReport,
    TopologyReport,
)
from app.agent.golden_signals import (
    analyze_golden_signals,
    format_golden_signals,
    gaps_from_coverage,
)
from app.agent.guardrails import (
    apply_nightwatch_policy,
    metric_value_requested,
)
from app.agent.reports import (
    ToolExecution,
    build_steve_report,
    build_telemetry_report,
    build_topology_report,
)
from app.agent.specialists import emit_step, run_specialist
from app.agent.tools import execute_tool, get_skill
from app.config import get_runtime, runtime_bool, settings
from app.llm.client import fallback_ready

logger = logging.getLogger(__name__)

EventSink = Callable[[dict], Awaitable[None]]

# The design caps each specialist at 8-10 tools. TelemetryHound keeps the
# eight highest-signal tools; trace detail covers deep dives while the recent
# trace call already enriches error status.
TELEMETRY_TOOLS = {
    "doctor_metrics_names",
    "doctor_metrics_labels",
    "doctor_metrics_query",
    "doctor_logs_search",
    "doctor_logql_schema",
    "doctor_logql_query",
    "doctor_traces_recent",
    "doctor_trace_detail",
}

TOPOLOGY_TOOLS = {
    "guardian_overview",
    "guardian_partitions",
    "guardian_partition_detail",
    "guardian_intent_activity",
    "guardian_asset_describe",
    "doctor_event_analysis",
    "lb_edge_services",
}

STEVE_FLOW_TOOLS = {
    "monofs_search",
    "monofs_status",
    "workspace_status",
    "workspace_list",
    "workspace_read",
    "workspace_write",
    "workspace_diff",
    "guardian_intent_plan",
    "guardian_partition_validate",
    "doctor_graph_validate",
}

OTTO_EXEC_TOOLS = {
    "guardian_reconcile",
    "guardian_deploy_partition",
    "workspace_push",
}

GENERAL_TOOLS = {
    "datetime_now",
    "calculate",
    "web_search",
    "web_fetch",
    "strata_view_context",
    "agent_sso_status",
    "nightwatch_overview",
    "nightwatch_incidents",
    "nightwatch_activity",
    "nightwatch_incident_detail",
    "lb_edge_services",
}

TELEMETRY_PROMPT = """You are TelemetryHound, a Doctor telemetry specialist.
Your only job is to collect evidence about logs, metrics, and traces.
- Scope every log and trace query to the incident window supplied in the task
  context. Never query all history.
- A deterministic baseline pack already covers error/warn logs, recent traces,
  request traffic, 5xx rate, latency, and saturation for the partition. Do not
  re-run those broad queries; spend your calls on drill-downs or gaps.
- Discover metric names before writing PromQL when the metric is unknown.
- Query metrics, logs, and traces independently. Empty logs never excuse
  skipping traces, and vice versa.
- Prefer one strong query per signal over a catalog dump. Never repeat a query
  that already ran in this investigation.
- Never invent values, pod names, trace IDs, timestamps, or log lines.
- Finish with a terse evidence brief: what you queried, what was found, and
  which signals were empty. Name concrete error strings verbatim.
"""

TOPOLOGY_PROMPT = """You are TopologyAuditor, a Guardian topology specialist.
Your only job is to inspect deployment state, intent drift, asset health, and
recent Guardian events.
- A deterministic baseline pack already covers guardian_overview,
  guardian_partition_detail for the partition, and doctor_event_analysis. Do
  not re-run those same broad calls; drill into specific intents/assets.
- Use guardian_partition_detail with intent/asset filters before concluding
  that a field or asset is missing.
- Report drift, failing assets, and cross-partition targets explicitly.
- Never invent partition, intent, asset, or event data.
- Finish with a terse topology brief grounded in tool results. Quote concrete
  failure messages (for example an APPLY error) verbatim.
"""

OTTO_PROMPT = """You are Outage Otto, the principal SRE incident commander.
You receive structured TelemetryReport and TopologyReport JSON produced by
specialists, plus their first-hand specialist_briefs. Both specialists have
finished; the payload is complete. You do not have tools. Synthesize a verdict
from all of the evidence.

Rules:
- Do not invent evidence, metric values, services, or trace IDs.
- The specialist_briefs are direct notes from the agents that ran the tools.
  Treat concrete error strings in them or in topology.recent_events as
  evidence, and quote them instead of falling back to "no signal".
- If the evidence is weak, lower confidence and recommend escalation.
- If the root cause is outside the alerting partition, use action "handoff".
- Treat the golden_signals coverage report as part of the evidence. Never claim
  latency, traffic, errors, or saturation is covered unless covered=true.
- Return strict JSON only, with no markdown fences.
- Write otto_response in a dry, technical SRE voice: short, concrete, no
  roleplay filler and no claims beyond the reports.

JSON shape:
{
  "summary": "one or two sentences",
  "root_cause_partition": "partition",
  "root_cause_service": "service or component",
  "confidence": 0.0,
  "cross_partition": false,
  "escalate": false,
  "escalation_target": "",
  "escalation_reason": "",
  "recommended_action": "none|notify|ack|silence|escalate|handoff|reconcile|restart|scale|rollback|patch_code",
  "recommendation_target": "",
  "rationale": "why, citing evidence",
  "falsification": "what would prove this wrong",
  "evidence_ids": ["ev-example-0"],
  "candidates": [{"partition": "", "service": "", "score": 0.0, "confidence": 0.0, "rationale": ""}],
  "improvements": [{"title": "", "category": "metrics|logs|traces|labels|alerting|dashboard|docs", "rationale": "", "proposed_change": "", "target": "", "confidence": 0.0}],
  "otto_response": "final operator-facing answer"
}
"""

STEVE_PROMPT = """You are Software Steve, a senior software engineer in the
Strata MonoFS workspace. Locate the relevant code, read it, make the smallest
safe change, and show the diff.
- Use monofs_search before workspace_read; do not guess file paths.
- Write complete files with workspace_write.
- Run workspace_diff after editing.
- Validate graph/dashboard files with doctor_graph_validate and partition
  bundles with guardian_partition_validate when relevant.
- Do not commit or push; this is a proposal-first flow.
- Never claim a change exists without a tool result confirming it.
"""

_MUTATION_RE = re.compile(
    r"\b(?:please\s+)?(?:reconcile|deploy|rollback|restart|scale|apply|push)\b",
    re.IGNORECASE,
)
_MUTATION_QUESTION_RE = re.compile(
    r"\b(?:why|how|what|explain|plan|proposal|failed|failure|error|status)\b",
    re.IGNORECASE,
)
_OPS_RE = re.compile(
    r"\b(?:incident|outage|alert|telemetry|logs?|traces?|spans?|metrics?|"
    r"latency|p9[59]|5xx|health|drift|partition|intent|asset|guardian|doctor|"
    r"monofs|crashloop|oom|deploy|deployment|reconcile|slo|error rate)\b",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"\b(?:code|codebase|repo|repository|bug|fix|patch|implement|refactor|"
    r"function|method|class|test|pytest|compile|typescript|python|golang|"
    r"react|sql|schema|migration|diff|commit|branch|pull request|handler)\b",
    re.IGNORECASE,
)
_VIEW_RE = re.compile(
    r"^\s*View context:\s*([A-Za-z-]+)\s*[·|]\s*([^\n]+)", re.IGNORECASE
)
_DEPLOY_PATH_RE = re.compile(r"(\.{0,2}/[\w./-]*partitions/[\w./-]+)")
_PARTITION_RE = re.compile(r"\bpartition\s*[=:]\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)


@dataclass
class FlowResult:
    route: str
    text: str
    telemetry: TelemetryReport | None = None
    topology: TopologyReport | None = None
    verdict: IncidentVerdict | None = None
    steve: SteveProposalReport | None = None


@dataclass
class PendingApproval:
    id: str
    session_id: str
    tool: str
    args: dict
    rationale: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


_pending_approvals: dict[str, PendingApproval] = {}


def has_mutation_signal(message: str) -> bool:
    text = message or ""
    if not _MUTATION_RE.search(text):
        return False
    return not _MUTATION_QUESTION_RE.search(text)


def classify_route(message: str, persona_id: str = "otto") -> str:
    """Choose a deterministic route for a turn."""
    text = message or ""
    if has_mutation_signal(text):
        return "mutate_infra"
    ops = bool(_OPS_RE.search(text))
    code = bool(_CODE_RE.search(text))
    if persona_id == "steve":
        return "code_fix"
    if ops and code:
        return "crew_collab"
    if ops:
        return "triage"
    if code:
        return "code_fix"
    return "general"


def _partition_hint(message: str) -> str:
    match = _PARTITION_RE.search(message or "")
    if match:
        return match.group(1).lower()
    return loop_mod._partition_hint(message or "")


_ALERT_START_RE = re.compile(
    r"(?:started(?:\s*at)?|starts?[_ ]?at|start[_ ]?time|since)\s*[:=]?\s*"
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))",
    re.IGNORECASE,
)
_RFC3339_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b"
)


def _parse_rfc3339(raw: str) -> datetime | None:
    """Parse an RFC3339 timestamp, tolerating nanosecond fractions."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python only accepts microsecond precision; clip longer fractions.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_window_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _incident_window(
    message: str, *, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Anchor triage queries to the alert's start time.

    Prefer an explicit "Started:"/"since" timestamp from the alert payload, then
    the earliest plausible timestamp in the message. Fall back to a bounded
    lookback so a vague request never turns into an all-history scan.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        lookback = int(get_runtime("triage_lookback_minutes") or 30)
    except (TypeError, ValueError):
        lookback = 30
    try:
        max_window = int(get_runtime("triage_max_window_minutes") or 120)
    except (TypeError, ValueError):
        max_window = 120
    try:
        preroll_minutes = int(get_runtime("triage_preroll_minutes") or 5)
    except (TypeError, ValueError):
        preroll_minutes = 5
    lookback = max(5, lookback)
    max_window = max(lookback, max_window)
    preroll = timedelta(minutes=max(0, preroll_minutes))

    start: datetime | None = None
    match = _ALERT_START_RE.search(message or "")
    if match:
        start = _parse_rfc3339(match.group(1))
    if start is None:
        candidates = [
            parsed
            for parsed in (
                _parse_rfc3339(item.group(1))
                for item in _RFC3339_RE.finditer(message or "")
            )
            if parsed is not None
            and now - timedelta(days=2) <= parsed <= now + timedelta(minutes=5)
        ]
        if candidates:
            start = min(candidates)

    explicit_start = start is not None and start <= now
    if not explicit_start:
        start = now - timedelta(minutes=lookback)
    else:
        # The trigger usually precedes the alert by a few seconds. Start just
        # before the alert so the causal event is inside the window.
        start -= preroll
    if now - start > timedelta(minutes=max_window):
        start = now - timedelta(minutes=max_window)
    if now - start < timedelta(seconds=15):
        # Keep a non-zero query range for very fresh alerts.
        start = now - timedelta(seconds=15)
    return start, now


def _loads(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _extract_json(text: str) -> dict | None:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


_JSON_STRING_FIELDS = (
    "summary",
    "root_cause_partition",
    "root_cause_service",
    "escalation_target",
    "escalation_reason",
    "recommended_action",
    "recommendation_target",
    "rationale",
    "falsification",
    "otto_response",
)


def _extract_partial_json_fields(text: str) -> dict:
    """Recover the leading string fields from a truncated verdict payload.

    Small models occasionally exceed the synthesis token budget, leaving a
    valid prefix of the verdict JSON. Falling back to the raw text dumps that
    JSON into the chat window; recovering the named fields keeps the operator
    response structured.
    """
    cleaned = (text or "").strip()
    if not cleaned.startswith("{"):
        return {}
    out: dict = {}
    for key in _JSON_STRING_FIELDS:
        match = re.search(
            rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned
        )
        if match:
            try:
                out[key] = json.loads(f'"{match.group(1)}"')
            except json.JSONDecodeError:
                out[key] = match.group(1)
    confidence = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', cleaned)
    if confidence:
        try:
            out["confidence"] = float(confidence.group(1))
        except ValueError:
            pass
    return out


def _looks_like_json_blob(text: str) -> bool:
    cleaned = (text or "").strip()
    return cleaned.startswith("{") or cleaned.startswith("```json")


def _render_verdict_markdown(verdict: IncidentVerdict) -> str:
    """Render a readable operator report from the structured verdict."""
    lines: list[str] = [verdict.summary.strip() or "No summary available."]
    prose = (verdict.otto_response or "").strip()
    if prose and not _looks_like_json_blob(prose) and prose != verdict.summary.strip():
        lines.append(prose)

    lines.append("")
    lines.append("**Root cause**")
    root = verdict.root_cause_service or "undetermined"
    partition = verdict.root_cause_partition or "unknown partition"
    lines.append(f"- Service: `{root}`")
    lines.append(f"- Partition: `{partition}`")
    lines.append(f"- Confidence: {verdict.confidence:.0%}")

    lines.append("")
    lines.append("**Recommended action**")
    action = verdict.recommended_action or "none"
    target = verdict.recommendation_target or partition
    lines.append(f"- `{action}` → `{target}`")
    if verdict.escalate:
        lines.append(
            f"- Escalate to `{verdict.escalation_target or 'owning team'}`"
            + (
                f": {verdict.escalation_reason}"
                if verdict.escalation_reason
                else ""
            )
        )

    if verdict.rationale:
        lines.append("")
        lines.append("**Why**")
        lines.append(verdict.rationale.strip())

    if verdict.falsification:
        lines.append("")
        lines.append("**What would prove this wrong**")
        lines.append(verdict.falsification.strip())

    if verdict.candidates:
        lines.append("")
        lines.append("**Candidates**")
        for candidate in verdict.candidates[:5]:
            label = " / ".join(
                part
                for part in (
                    candidate.partition,
                    candidate.service,
                )
                if part
            ) or "unnamed"
            lines.append(
                f"- `{label}` — score {candidate.score:.2f}, "
                f"confidence {candidate.confidence:.0%}"
            )
            if candidate.rationale:
                lines.append(f"  {candidate.rationale.strip()}")

    if verdict.improvements:
        lines.append("")
        lines.append("**Observability follow-ups**")
        for item in verdict.improvements[:6]:
            lines.append(
                f"- **{item.title}** ({item.category}) — "
                f"{item.proposed_change or item.rationale}"
            )

    return "\n".join(lines).strip()


async def _history_context(session_id: str, limit: int = 6) -> str:
    try:
        messages = await session_mgr.get_messages(session_id)
    except Exception:
        return ""
    lines = []
    for message in messages[-limit:]:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:600]}")
    return "\n".join(lines)


async def _emit_text(emit: EventSink | None, text: str, session_id: str) -> None:
    if emit is None or not text:
        return
    for chunk in loop_mod._text_chunks(text):
        await emit({"type": "token", "data": chunk, "session_id": session_id})


async def _legacy_turn(
    user_message: str,
    session_id: str,
    *,
    persona_id: str,
    profile: str,
    emit: EventSink | None,
    allowed_override: set[str] | None = None,
) -> str:
    if emit is None:
        return await loop_mod.run_agent_turn(
            user_message,
            session_id,
            stream=False,
            persona_id=persona_id,
            profile=profile,
            allowed_override=allowed_override,
        )
    stream = await loop_mod.run_agent_turn(
        user_message,
        session_id,
        stream=True,
        persona_id=persona_id,
        profile=profile,
        allowed_override=allowed_override,
    )
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
        await emit({"type": "token", "data": chunk, "session_id": session_id})
    return "".join(parts)


async def _view_context(message: str) -> tuple[str, str | None]:
    """Run the Ask Strata starter tool when the message carries a view context."""
    match = _VIEW_RE.search(message or "")
    if not match:
        return message, None
    product = match.group(1).strip()
    path = match.group(2).strip()
    try:
        result = await execute_tool(
            "strata_view_context", {"product": product, "path": path}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("View context starter failed: %s", exc)
        return message, None
    return message, result


def _fallback_verdict(
    *,
    message: str,
    partition: str,
    telemetry: TelemetryReport,
    topology: TopologyReport,
) -> IncidentVerdict:
    evidence_count = len(telemetry.evidence) + len(topology.evidence)
    service = (
        topology.failing_assets[0]
        if topology.failing_assets
        else (telemetry.error_logs[0][:80] if telemetry.error_logs else "unknown")
    )
    if topology.cross_partition_targets:
        action = "handoff"
    elif topology.drifted_intents:
        action = "reconcile"
    elif telemetry.failing_spans or telemetry.error_logs:
        action = "notify"
    elif _CODE_RE.search(message or ""):
        action = "patch_code"
    else:
        action = "none"

    confidence = 0.6 if evidence_count else 0.2
    summary = (
        f"{partition or 'Partition scope unknown'}: "
        f"{len(telemetry.anomalous_metrics)} notable metric queries, "
        f"{len(telemetry.error_logs)} error log highlights, "
        f"{len(telemetry.failing_spans)} failing span groups; "
        f"topology is {topology.health_status}."
    )
    rationale = (
        "Derived deterministically from specialist tool output because the "
        "synthesis model did not return a valid verdict."
    )
    otto_response = (
        f"{summary}\n\n"
        "No safe automatic change was made. "
        + (
            "The candidate root cause is outside the alerting partition; "
            "hand this to the owning partition."
            if action == "handoff"
            else "Confirm the evidence and choose the smallest reversible action."
        )
    )
    return IncidentVerdict(
        summary=summary,
        root_cause_partition=partition,
        root_cause_service=service,
        confidence=confidence,
        cross_partition=action == "handoff",
        escalate=action in {"handoff", "escalate"},
        escalation_target=(
            topology.cross_partition_targets[0]
            if topology.cross_partition_targets
            else ""
        ),
        escalation_reason=(
            "root cause appears to be outside the alerting partition"
            if action == "handoff"
            else ""
        ),
        recommended_action=action,  # type: ignore[arg-type]
        recommendation_target=service,
        rationale=rationale,
        otto_response=otto_response,
    )


def _merge_verdict(
    base: IncidentVerdict, payload: dict | None, raw_text: str
) -> IncidentVerdict:
    if not isinstance(payload, dict):
        payload = _extract_partial_json_fields(raw_text)
    if not isinstance(payload, dict) or not payload:
        # Never echo an unparseable JSON blob into the chat. The fallback
        # verdict already contains a deterministic, readable summary.
        if raw_text.strip() and not _looks_like_json_blob(raw_text):
            base.otto_response = raw_text.strip()
        return base

    allowed_actions = {
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
    }
    for key in (
        "summary",
        "root_cause_partition",
        "root_cause_service",
        "escalation_target",
        "escalation_reason",
        "recommendation_target",
        "rationale",
        "falsification",
        "otto_response",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            setattr(base, key, value.strip())
    for key in ("cross_partition", "escalate"):
        value = payload.get(key)
        if isinstance(value, bool):
            setattr(base, key, value)
    try:
        base.confidence = max(0.0, min(1.0, float(payload.get("confidence"))))
    except (TypeError, ValueError):
        pass
    action = str(payload.get("recommended_action") or "").strip().lower()
    if action in allowed_actions:
        base.recommended_action = action  # type: ignore[assignment]

    evidence_ids = payload.get("evidence_ids")
    if isinstance(evidence_ids, list):
        base.evidence_ids = [str(item) for item in evidence_ids if str(item).strip()][:20]

    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        parsed: list[RootCauseCandidate] = []
        for item in candidates[:5]:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(
                    RootCauseCandidate(
                        partition=str(item.get("partition") or base.root_cause_partition),
                        service=str(item.get("service") or "unknown"),
                        score=float(item.get("score") or 0.0),
                        confidence=float(item.get("confidence") or 0.0),
                        rationale=str(item.get("rationale") or ""),
                    )
                )
            except Exception:
                continue
        if parsed:
            base.candidates = parsed

    improvements = payload.get("improvements")
    if isinstance(improvements, list):
        parsed_gaps: list[ObservabilityGap] = []
        for item in improvements[:6]:
            if not isinstance(item, dict):
                continue
            try:
                parsed_gaps.append(ObservabilityGap(**item))
            except Exception:
                continue
        if parsed_gaps:
            base.improvements = parsed_gaps
    return base


_SYNTHESIS_MAX_CHARS = 18000
_SYNTHESIS_EVIDENCE_LIMIT = 20
_SYNTHESIS_BRIEF_LIMIT = 2400
_HIGH_SIGNAL_RE = re.compile(
    r"\b(error|fail(?:ed|ure)?|panic|fatal|timeout|unavailable|apply|invalid|"
    r"5\d\d|oom|crashloop)\b",
    re.IGNORECASE,
)


def _prioritized_evidence(records: object, limit: int) -> list[dict]:
    """Keep the evidence most likely to explain a failure inside the budget."""
    if not isinstance(records, list):
        return []
    items = [item for item in records if isinstance(item, dict)]
    high = [
        item
        for item in items
        if _HIGH_SIGNAL_RE.search(str(item.get("result_summary") or ""))
        or _HIGH_SIGNAL_RE.search(str(item.get("query") or ""))
    ]
    rest = [item for item in items if item not in high]
    return (high + rest)[:limit]


def _bounded_report_dump(report: object, limit: int) -> dict:
    dump = report.model_dump()  # type: ignore[attr-defined]
    dump["evidence"] = _prioritized_evidence(dump.get("evidence"), limit)
    return dump


def _synthesis_payload(
    *,
    message: str,
    partition: str,
    telemetry: TelemetryReport,
    topology: TopologyReport,
    golden_signals: list[GoldenSignalCoverage] | None,
    specialist_briefs: dict[str, str] | None,
    incident_window: tuple[datetime, datetime] | None,
) -> dict:
    """Build a complete but bounded packet for Otto.

    The old code truncated the serialized packet at 12k characters, which could
    silently drop late evidence (for example the Guardian APPLY error). Shrink
    the least important evidence instead, and keep the specialist briefs and
    topology events intact so synthesis sees what the specialists found.
    """
    briefs = {
        str(agent): str(text).strip()[: _SYNTHESIS_BRIEF_LIMIT]
        for agent, text in (specialist_briefs or {}).items()
        if str(text or "").strip()
    }
    window = (
        {
            "start": _format_window_ts(incident_window[0]),
            "end": _format_window_ts(incident_window[1]),
        }
        if incident_window
        else {}
    )
    signals = [item.model_dump() for item in (golden_signals or [])]
    for evidence_limit in (_SYNTHESIS_EVIDENCE_LIMIT, 12, 6, 3):
        payload = {
            "request": message,
            "alerting_partition": partition,
            "incident_window": window,
            "specialist_briefs": briefs,
            "topology": _bounded_report_dump(topology, evidence_limit),
            "telemetry": _bounded_report_dump(telemetry, evidence_limit),
            "golden_signals": signals,
        }
        if len(json.dumps(payload, default=str)) <= _SYNTHESIS_MAX_CHARS:
            return payload

    # Last resort: keep the briefs and topology events, shed request/evidence.
    return {
        "request": (message or "")[:2000],
        "alerting_partition": partition,
        "incident_window": window,
        "specialist_briefs": briefs,
        "topology": {
            "partition": topology.partition,
            "health_status": topology.health_status,
            "drifted_intents": topology.drifted_intents,
            "failing_assets": topology.failing_assets,
            "recent_events": topology.recent_events,
            "cross_partition_targets": topology.cross_partition_targets,
        },
        "telemetry": {
            "partition": telemetry.partition,
            "metrics_queried": telemetry.metrics_queried,
            "anomalous_metrics": telemetry.anomalous_metrics,
            "error_logs": telemetry.error_logs,
            "failing_spans": telemetry.failing_spans,
            "empty_signals": telemetry.empty_signals,
        },
        "golden_signals": signals,
    }


async def _synthesize_verdict(
    *,
    message: str,
    partition: str,
    telemetry: TelemetryReport,
    topology: TopologyReport,
    emit: EventSink | None,
    task_name: str = "outage_otto.synthesize",
    profile: str | None = None,
    golden_signals: list[GoldenSignalCoverage] | None = None,
    specialist_briefs: dict[str, str] | None = None,
    incident_window: tuple[datetime, datetime] | None = None,
) -> IncidentVerdict:
    await emit_step(emit, "outage_otto", task_name, "started")
    fallback = _fallback_verdict(
        message=message,
        partition=partition,
        telemetry=telemetry,
        topology=topology,
    )
    report_payload = _synthesis_payload(
        message=message,
        partition=partition,
        telemetry=telemetry,
        topology=topology,
        golden_signals=golden_signals,
        specialist_briefs=specialist_briefs,
        incident_window=incident_window,
    )
    profile = profile or ("fallback" if fallback_ready() else "local")
    messages = [
        {"role": "system", "content": OTTO_PROMPT},
        {
            "role": "user",
            "content": json.dumps(report_payload, default=str),
        },
    ]
    raw = ""
    try:
        raw = await loop_mod.chat_completion(
            messages,
            temperature=0.2,
            max_tokens=2400,
            stream=False,
            profile=profile,
        )
    except Exception as exc:  # pragma: no cover - provider failure path
        logger.warning("Otto synthesis failed: %s", exc)
        fallback.escalation_reason = (
            fallback.escalation_reason or f"synthesis unavailable: {exc}"
        )
        if golden_signals:
            fallback.golden_signals = golden_signals
            fallback.improvements.extend(
                gaps_from_coverage(golden_signals, partition)
            )
        await emit_step(
            emit, "outage_otto", task_name, "failed", summary=str(exc)
        )
        return fallback

    verdict = _merge_verdict(fallback, _extract_json(raw), raw)
    verdict = apply_nightwatch_policy(
        verdict,
        partition,
        escalation_confidence=float(
            get_runtime("nightwatch_escalation_confidence")
            or settings.nightwatch_escalation_confidence
        ),
    )
    if golden_signals:
        verdict.golden_signals = golden_signals
        seen = {gap.title for gap in verdict.improvements}
        for gap in gaps_from_coverage(golden_signals, partition):
            if gap.title not in seen:
                verdict.improvements.append(gap)
                seen.add(gap.title)
    if not verdict.improvements:
        verdict.improvements = list(telemetry.observability_gaps)
    await emit_step(
        emit,
        "outage_otto",
        task_name,
        "completed",
        summary=(
            verdict.summary
            if _looks_like_json_blob(verdict.otto_response)
            else (verdict.otto_response or verdict.summary)
        ),
    )
    return verdict


def _triage_telemetry_specs(
    partition: str, start: datetime, end: datetime
) -> list[tuple[str, dict]]:
    """Queries every triage needs, scoped to the incident window.

    Keep this pack small: each entry is a request to Doctor. The specialists
    drill down from here instead of re-running broad scans.
    """
    window = {
        "from": _format_window_ts(start),
        "to": _format_window_ts(end),
    }
    return [
        (
            "doctor_logql_query",
            {
                "query": (
                    f'{{guardian_partition="{partition}"}}'
                    ' |~ "(?i)error|fail|panic|timeout|unavailable"'
                ),
                "start": window["from"],
                "end": window["to"],
                "limit": 50,
            },
        ),
        (
            "doctor_traces_recent",
            {
                "partition": partition,
                "limit": 10,
                "include_span_status": True,
                **window,
            },
        ),
        (
            "doctor_metrics_query",
            {
                "promql": (
                    f'sum(rate(http_server_request_duration_count{{guardian_partition="{partition}"}}[5m]))'
                    f' or sum(rate(http_server_request_duration_seconds_count{{guardian_partition="{partition}"}}[5m]))'
                ),
                "compact": True,
            },
        ),
        (
            "doctor_metrics_query",
            {
                "promql": (
                    f'sum(rate(http_server_request_duration_count{{guardian_partition="{partition}",http_response_status_code=~"5.."}}[5m]))'
                    f' or sum(rate(http_server_request_duration_seconds_count{{guardian_partition="{partition}",http_response_status_code=~"5.."}}[5m]))'
                ),
                "compact": True,
            },
        ),
        (
            "doctor_metrics_query",
            {
                "promql": (
                    f'histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_bucket{{guardian_partition="{partition}"}}[5m])))'
                    f' or histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{{guardian_partition="{partition}"}}[5m])))'
                ),
                "compact": True,
            },
        ),
        (
            "doctor_metrics_query",
            {
                "promql": (
                    f'max by (k8s_pod_name) (k8s_top_pod_cpu_usage_millicores{{k8s_namespace_name="div-demo"}})'
                    if partition in {"diva-shared", "divb-shared"}
                    else (
                        f'sum by (service) (rate(process_cpu_seconds_total{{guardian_partition="{partition}"}}[5m]))'
                        f' or sum(rate(container_cpu_usage_seconds_total{{guardian_partition="{partition}"}}[5m]))'
                    )
                ),
                "compact": True,
            },
        ),
    ]


def _triage_topology_specs(partition: str) -> list[tuple[str, dict]]:
    return [
        ("guardian_overview", {}),
        ("guardian_partition_detail", {"partition": partition}),
        ("doctor_event_analysis", {"partition": partition, "tenant": "default", "limit": 50}),
    ]


def _parallel_query_limit() -> int:
    try:
        value = int(get_runtime("triage_max_parallel_queries") or 3)
    except (TypeError, ValueError):
        value = 3
    return max(1, value)


async def _collect_tool_evidence(
    *,
    agent_id: str,
    task_name: str,
    specs: list[tuple[str, dict]],
    allowed: set[str],
    emit: EventSink | None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[ToolExecution]:
    """Run deterministic evidence queries so Otto never depends on model luck.

    Requests are bounded by ``triage_max_parallel_queries`` so an investigation
    cannot stampede Doctor/Guardian with a dozen simultaneous scans. Callers
    that run two collectors concurrently should pass a shared semaphore so the
    cap is global, not per-collector.
    """
    started = time.perf_counter()
    semaphore = semaphore or asyncio.Semaphore(_parallel_query_limit())

    async def one(name: str, args: dict) -> ToolExecution:
        async with semaphore:
            tool_started = time.perf_counter()
            await emit_step(
                emit,
                agent_id,
                task_name,
                "tool_call",
                tool=name,
                tool_args=args,
                summary=name,
            )
            output = await execute_tool(name, args, allowed=allowed)
            elapsed = int((time.perf_counter() - tool_started) * 1000)
            await emit_step(
                emit,
                agent_id,
                task_name,
                "tool_result",
                tool=name,
                summary=output,
            )
        logger.info(
            "[triage] %s %s %dms args=%s result=%dB",
            agent_id,
            name,
            elapsed,
            json.dumps(args, default=str)[:300],
            len(output or ""),
        )
        return ToolExecution(name=name, args=args, result=output)

    executions = await asyncio.gather(*(one(name, args) for name, args in specs))
    evidence = list(executions)

    # If the trace summary reports errors, fetch the waterfall for Otto. This
    # is deterministic and does not depend on the model choosing a trace ID.
    if "doctor_trace_detail" in allowed:
        trace_ids: list[str] = []
        for execution in executions:
            if execution.name != "doctor_traces_recent":
                continue
            try:
                payload = json.loads(execution.result)
            except (TypeError, ValueError):
                continue
            for trace in payload.get("traces", []) if isinstance(payload, dict) else []:
                if not isinstance(trace, dict):
                    continue
                if int(trace.get("error_count") or 0) > 0 and trace.get("trace_id"):
                    trace_ids.append(str(trace["trace_id"]))
        for trace_id in trace_ids[:2]:
            evidence.append(await one("doctor_trace_detail", {"trace_id": trace_id}))

    await emit_step(
        emit,
        agent_id,
        task_name,
        "completed",
        summary=(
            f"deterministic evidence: {len(evidence)} queries in "
            f"{int((time.perf_counter() - started) * 1000)}ms"
        ),
    )
    return evidence


def _merge_executions(*groups: list[ToolExecution]) -> list[ToolExecution]:
    out: list[ToolExecution] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for execution in group:
            key = (execution.name, json.dumps(execution.args, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            out.append(execution)
    return out


async def _run_diagnostic(
    *,
    message: str,
    session_id: str,
    profile: str,
    emit: EventSink | None,
    context: str,
) -> tuple[
    TelemetryReport, TopologyReport, IncidentVerdict, list[GoldenSignalCoverage]
]:
    partition = _partition_hint(message)
    requests_metric = metric_value_requested(message)
    window_start, window_end = _incident_window(message)
    window_note = (
        f"Incident window (UTC): {_format_window_ts(window_start)} to "
        f"{_format_window_ts(window_end)}. Scope every log and trace query to "
        "this window; do not scan older history."
    )
    scoped_message = f"{message}\n\n{window_note}"
    telemetry_task = run_specialist(
        agent_id="telemetry_hound",
        system_prompt=TELEMETRY_PROMPT,
        allowed=TELEMETRY_TOOLS,
        user_message=scoped_message,
        profile="local",
        emit=emit,
        task_name="telemetry_hound.collect",
        context=context,
        require_tools=True,
        metric_value=requests_metric,
        partition_hint=partition,
    )
    topology_task = run_specialist(
        agent_id="topology_auditor",
        system_prompt=TOPOLOGY_PROMPT,
        allowed=TOPOLOGY_TOOLS,
        user_message=scoped_message,
        profile="local",
        emit=emit,
        task_name="topology_auditor.audit",
        context=context,
        require_tools=True,
        partition_hint=partition,
    )
    deterministic_task = None
    if partition:
        evidence_semaphore = asyncio.Semaphore(_parallel_query_limit())
        deterministic_task = asyncio.gather(
            _collect_tool_evidence(
                agent_id="telemetry_hound",
                task_name="telemetry_hound.verify",
                specs=_triage_telemetry_specs(partition, window_start, window_end),
                allowed=TELEMETRY_TOOLS,
                emit=emit,
                semaphore=evidence_semaphore,
            ),
            _collect_tool_evidence(
                agent_id="topology_auditor",
                task_name="topology_auditor.verify",
                specs=_triage_topology_specs(partition),
                allowed=TOPOLOGY_TOOLS,
                emit=emit,
                semaphore=evidence_semaphore,
            ),
        )
    # Make the ordering explicit in the crew timeline: Otto does not start
    # synthesizing until every specialist and deterministic query has returned.
    await emit_step(
        emit,
        "outage_otto",
        "outage_otto.await_evidence",
        "started",
        summary="Waiting for TelemetryHound and TopologyAuditor to finish",
    )
    if deterministic_task is None:
        telemetry_result, topology_result = await asyncio.gather(
            telemetry_task, topology_task
        )
        deterministic_telemetry: list[ToolExecution] = []
        deterministic_topology: list[ToolExecution] = []
    else:
        (
            telemetry_result,
            topology_result,
            (deterministic_telemetry, deterministic_topology),
        ) = await asyncio.gather(telemetry_task, topology_task, deterministic_task)
    telemetry_execs = _merge_executions(
        telemetry_result.executions, deterministic_telemetry
    )
    topology_execs = _merge_executions(
        topology_result.executions, deterministic_topology
    )
    logger.info(
        "[triage] evidence partition=%s telemetry=%d topology=%d "
        "specialist_errors=%s/%s",
        partition,
        len(telemetry_execs),
        len(topology_execs),
        telemetry_result.errors,
        topology_result.errors,
    )
    await emit_step(
        emit,
        "outage_otto",
        "outage_otto.await_evidence",
        "completed",
        summary=(
            f"Evidence complete: TelemetryHound {len(telemetry_execs)} tool calls, "
            f"TopologyAuditor {len(topology_execs)} tool calls"
        ),
    )
    telemetry = build_telemetry_report(partition, telemetry_execs)
    topology = build_topology_report(partition, topology_execs)
    golden_signals = analyze_golden_signals(telemetry, topology)
    verdict = await _synthesize_verdict(
        message=message,
        partition=partition,
        telemetry=telemetry,
        topology=topology,
        emit=emit,
        golden_signals=golden_signals,
        specialist_briefs={
            "telemetry_hound": telemetry_result.text,
            "topology_auditor": topology_result.text,
        },
        incident_window=(window_start, window_end),
    )
    return telemetry, topology, verdict, golden_signals


def _verification_command(files: list[str]) -> str:
    for path in files:
        if path.endswith(".go"):
            return "go test ./..."
        if path.endswith(".py"):
            return "python -m pytest"
        if path.endswith((".ts", ".tsx")):
            return "npm run build"
        if path.endswith((".yaml", ".yml", ".json")):
            return "guardianctl partition validate-local --root ./stratatools/partitions"
    return "Run the repository's test suite and review the diff."


async def _validate_steve_changes(
    files: list[str], emit: EventSink | None
) -> str:
    if not files:
        return "No files changed."
    results: list[str] = []
    for path in files[:2]:
        lowered = path.lower()
        await emit_step(
            emit,
            "software_steve",
            "spec_validator.validate",
            "started",
            summary=path,
        )
        if "dashboard" in lowered or "graph" in lowered:
            output = await execute_tool(
                "doctor_graph_validate", {"path": path}, allowed=STEVE_FLOW_TOOLS
            )
            results.append(f"doctor_graph_validate({path}): {output[:600]}")
        elif "partitions/" in lowered:
            root = path.split("partitions/", 1)[0] + "partitions"
            output = await execute_tool(
                "guardian_partition_validate",
                {"root": root or "./stratatools/partitions"},
                allowed=STEVE_FLOW_TOOLS,
            )
            results.append(f"guardian_partition_validate({root}): {output[:600]}")
        await emit_step(
            emit,
            "software_steve",
            "spec_validator.validate",
            "completed",
            summary=results[-1] if results else "not applicable",
        )
    return "\n".join(results) or "No deterministic validator applies to these files."


async def _run_steve(
    *,
    message: str,
    session_id: str,
    profile: str,
    emit: EventSink | None,
    context: str,
    verdict: IncidentVerdict | None = None,
) -> tuple[str, SteveProposalReport]:
    extra = ""
    if verdict is not None:
        extra = (
            "\n\nOtto's diagnosis for context:\n"
            f"{verdict.summary}\nRoot cause: "
            f"{verdict.root_cause_partition}/{verdict.root_cause_service}\n"
            "Implement the smallest safe fix or improvement proposal."
        )
    result = await run_specialist(
        agent_id="software_steve",
        system_prompt=STEVE_PROMPT,
        allowed=STEVE_FLOW_TOOLS,
        user_message=message + extra,
        profile=profile,
        emit=emit,
        task_name="software_steve.patch",
        context=context,
        require_tools=True,
    )

    diff = ""
    if "workspace_diff" in result.called_tools:
        for execution in result.executions:
            if execution.name == "workspace_diff":
                diff = execution.result
    else:
        await emit_step(
            emit,
            "software_steve",
            "software_steve.diff",
            "tool_call",
            tool="workspace_diff",
        )
        diff = await execute_tool("workspace_diff", {}, allowed=STEVE_FLOW_TOOLS)
        result.executions.append(
            ToolExecution(name="workspace_diff", args={}, result=diff)
        )
        await emit_step(
            emit,
            "software_steve",
            "software_steve.diff",
            "tool_result",
            tool="workspace_diff",
            summary=diff,
        )

    files = []
    for execution in result.executions:
        if execution.name == "workspace_write":
            path = str(execution.args.get("path") or "").strip()
            if path and path not in files:
                files.append(path)
    validation = await _validate_steve_changes(files, emit)
    report = build_steve_report(
        result.executions,
        result.text,
        unified_diff=diff,
        validation_result=validation,
        verification_command=_verification_command(files),
    )
    text = result.text.strip() or "No code change was produced."
    if report.unified_diff.strip() and "```diff" not in text:
        text += "\n\n### Proposed diff\n```diff\n" + report.unified_diff.strip() + "\n```"
    if report.validation_result and "No deterministic validator" not in report.validation_result:
        text += "\n\n### Validation\n" + report.validation_result
    if report.verification_command:
        text += "\n\n**Verify:** `" + report.verification_command + "`"
    return text, report


def _mutation_request(message: str, partition: str) -> tuple[str, dict] | None:
    lowered = (message or "").lower()
    if re.search(r"\bdeploy\b", lowered):
        if not partition:
            return None
        path_match = _DEPLOY_PATH_RE.search(message or "")
        partition_dir = (
            path_match.group(1)
            if path_match
            else f"./stratatools/partitions/{partition}"
        )
        return (
            "guardian_deploy_partition",
            {
                "partition_dir": partition_dir,
                "plan_only": False,
                "confirm": True,
            },
        )
    if re.search(r"\breconcile\b", lowered):
        if not partition:
            return None
        return ("guardian_reconcile", {"partition": partition, "confirm": True})
    if re.search(r"\bpush\b", lowered):
        return ("workspace_push", {"confirm": True})
    return None


def _register_approval(
    session_id: str, tool: str, args: dict, rationale: str
) -> PendingApproval:
    approval = PendingApproval(
        id=uuid.uuid4().hex[:12],
        session_id=session_id,
        tool=tool,
        args={key: value for key, value in args.items() if key != "confirm"},
        rationale=rationale,
    )
    _pending_approvals[approval.id] = approval
    return approval


def _workspace_approval(
    message: str, session_id: str, report: SteveProposalReport
) -> PendingApproval | None:
    """Request commit/push approval after Steve has produced a diff."""
    lowered = (message or "").lower()
    if re.search(r"\bpush\b", lowered):
        return _register_approval(
            session_id,
            "workspace_push",
            {},
            rationale=(
                "Steve produced local commits. Pushing is irreversible outside "
                "the overlay; approve to push upstream."
            ),
        )
    if re.search(r"\bcommit\b", lowered) and report.files_changed:
        return _register_approval(
            session_id,
            "workspace_commit",
            {"message": (message or "Apply approved workspace changes")[:200]},
            rationale=(
                "Steve produced a workspace diff. Approve to create a MonoFS "
                "virtual commit; it stays local until pushed."
            ),
        )
    return None


async def _announce_approval(
    emit: EventSink | None, approval: PendingApproval
) -> None:
    if emit is None:
        return
    await emit(
        {
            "type": "approval_required",
            "id": approval.id,
            "tool": approval.tool,
            "args": approval.args,
            "rationale": approval.rationale,
            "session_id": approval.session_id,
        }
    )


async def run_turn(
    user_message: str,
    session_id: str,
    *,
    persona_id: str = "otto",
    profile: str = "local",
    emit: EventSink | None = None,
    force_route: str | None = None,
) -> str:
    """Run one interactive turn through the deterministic flow."""
    if not runtime_bool("agent_flow_enabled", default=True):
        return await _legacy_turn(
            user_message,
            session_id,
            persona_id=persona_id,
            profile=profile,
            emit=emit,
        )

    if (user_message or "").strip().lower() == "/help":
        return await _legacy_turn(
            user_message,
            session_id,
            persona_id=persona_id,
            profile=profile,
            emit=emit,
        )

    route = force_route or classify_route(user_message, persona_id)
    if route == "general":
        # Ordinary conversation does not need the full 53-tool catalog.
        return await _legacy_turn(
            user_message,
            session_id,
            persona_id=persona_id,
            profile=profile,
            emit=emit,
            allowed_override=GENERAL_TOOLS,
        )

    context = await _history_context(session_id)
    user_message, view_result = await _view_context(user_message)
    if view_result:
        context = (context + "\n\n" + view_result).strip()

    await session_mgr.add_message(session_id, "user", user_message)
    partition = _partition_hint(user_message)

    if route == "mutate_infra":
        request = _mutation_request(user_message, partition)
        if request is None:
            text = (
                "I need a concrete partition before proposing a mutation. "
                "Name the partition (for example `partition=monofs`) or use "
                "`/api/agent/proposal` for an approval-first plan."
            )
            await session_mgr.add_message(session_id, "assistant", text, persona="otto")
            await _emit_text(emit, text, session_id)
            return text
        tool, args = request
        approval = _register_approval(
            session_id,
            tool,
            args,
            rationale=(
                "This tool mutates live infrastructure. Review the arguments, "
                "then approve or reject the execution."
            ),
        )
        text = (
            f"Approval required before `{tool}` runs on "
            f"{args.get('partition') or args.get('partition_dir') or 'the workspace'}.\n\n"
            "Review the request in the approval card. Nothing has been executed."
        )
        await session_mgr.add_message(session_id, "assistant", text, persona="otto")
        await _emit_text(emit, text, session_id)
        await _announce_approval(emit, approval)
        return text

    if route == "code_fix":
        text, report = await _run_steve(
            message=user_message,
            session_id=session_id,
            profile=profile,
            emit=emit,
            context=context,
        )
        approval = _workspace_approval(user_message, session_id, report)
        if approval is not None:
            text += (
                f"\n\nApproval required before `{approval.tool}` runs. "
                "Review the request in the approval card. Nothing has been committed or pushed."
            )
        await session_mgr.add_message(session_id, "assistant", text, persona="steve")
        await _emit_text(emit, text, session_id)
        if approval is not None:
            await _announce_approval(emit, approval)
        return text

    telemetry, topology, verdict, golden_signals = await _run_diagnostic(
        message=user_message,
        session_id=session_id,
        profile=profile,
        emit=emit,
        context=context,
    )
    text = _render_verdict_markdown(verdict)
    text = f"{text}\n\n{format_golden_signals(golden_signals)}"
    steve_report: SteveProposalReport | None = None
    approval: PendingApproval | None = None
    if route == "crew_collab" or verdict.recommended_action == "patch_code":
        steve_text, steve_report = await _run_steve(
            message=user_message,
            session_id=session_id,
            profile=profile,
            emit=emit,
            context=context,
            verdict=verdict,
        )
        text = f"{text}\n\n---\n\n## Software Steve\n{steve_text}"
        approval = _workspace_approval(user_message, session_id, steve_report)
        if approval is not None:
            text += (
                f"\n\nApproval required before `{approval.tool}` runs. "
                "Review the request in the approval card."
            )
    await session_mgr.add_message(session_id, "assistant", text, persona="otto")
    await _emit_text(emit, text, session_id)
    if approval is not None:
        await _announce_approval(emit, approval)
    logger.info(
        "[FLOW] route=%s partition=%s action=%s confidence=%.2f",
        route,
        partition,
        verdict.recommended_action,
        verdict.confidence,
    )
    return text


async def resolve_approval(
    approval_id: str,
    approved: bool,
    session_id: str | None = None,
) -> dict:
    """Execute or reject a pending mutation approval.

    Returns a small payload used by the WebSocket layer to stream the result.
    """
    request_session = str(session_id or "").strip()
    if not request_session:
        return {
            "approved": False,
            "text": "Approval request requires an authenticated session.",
        }
    approval = _pending_approvals.get(approval_id)
    if approval is None:
        return {
            "approved": False,
            "text": "This approval request has expired or was already handled.",
        }
    if request_session != approval.session_id:
        return {
            "approved": False,
            "text": "Approval request does not belong to this session.",
            "session_id": request_session,
        }
    _pending_approvals.pop(approval_id, None)
    if not approved:
        text = f"Rejected `{approval.tool}`. No state was changed."
        await session_mgr.add_message(approval.session_id, "assistant", text, persona="otto")
        return {"approved": False, "text": text, "session_id": approval.session_id}

    tool = approval.tool
    args = dict(approval.args)
    args["confirm"] = True
    skill = get_skill(tool)
    if skill is None:
        text = f"Unknown mutation tool `{tool}`."
        return {"approved": False, "text": text, "session_id": approval.session_id}

    await emit_step(None, "outage_otto", "outage_otto.execute", "started")
    result = await execute_tool(tool, args)
    text = (
        f"Approved and executed `{tool}`.\n\n"
        f"Result:\n```\n{result[:4000]}\n```"
    )
    await session_mgr.add_message(approval.session_id, "assistant", text, persona="otto")
    return {
        "approved": True,
        "text": text,
        "session_id": approval.session_id,
        "tool": tool,
        "result": result,
    }
