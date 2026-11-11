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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
- Discover metric names before writing PromQL when the metric is unknown.
- Query metrics, logs, and traces independently. Empty logs never excuse
  skipping traces, and vice versa.
- Prefer one strong query per signal over a catalog dump.
- Never invent values, pod names, trace IDs, timestamps, or log lines.
- Finish with a terse evidence brief: what you queried, what was found, and
  which signals were empty.
"""

TOPOLOGY_PROMPT = """You are TopologyAuditor, a Guardian topology specialist.
Your only job is to inspect deployment state, intent drift, asset health, and
recent Guardian events.
- Start with guardian_overview or guardian_partitions when scope is unclear.
- Use guardian_partition_detail with intent/asset filters before concluding
  that a field or asset is missing.
- Report drift, failing assets, and cross-partition targets explicitly.
- Never invent partition, intent, asset, or event data.
- Finish with a terse topology brief grounded in tool results.
"""

OTTO_PROMPT = """You are Outage Otto, the principal SRE incident commander.
You receive structured TelemetryReport and TopologyReport JSON produced by
specialists. You do not have tools. Synthesize a verdict from the evidence.

Rules:
- Do not invent evidence, metric values, services, or trace IDs.
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
        if raw_text.strip():
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
) -> IncidentVerdict:
    await emit_step(emit, "outage_otto", task_name, "started")
    fallback = _fallback_verdict(
        message=message,
        partition=partition,
        telemetry=telemetry,
        topology=topology,
    )
    report_payload = {
        "request": message,
        "alerting_partition": partition,
        "telemetry": telemetry.model_dump(),
        "topology": topology.model_dump(),
        "golden_signals": [item.model_dump() for item in (golden_signals or [])],
    }
    profile = profile or ("fallback" if fallback_ready() else "local")
    messages = [
        {"role": "system", "content": OTTO_PROMPT},
        {
            "role": "user",
            "content": json.dumps(report_payload, default=str)[:12000],
        },
    ]
    raw = ""
    try:
        raw = await loop_mod.chat_completion(
            messages,
            temperature=0.2,
            max_tokens=900,
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
        summary=verdict.otto_response or verdict.summary,
    )
    return verdict


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
    telemetry_task = run_specialist(
        agent_id="telemetry_hound",
        system_prompt=TELEMETRY_PROMPT,
        allowed=TELEMETRY_TOOLS,
        user_message=message,
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
        user_message=message,
        profile="local",
        emit=emit,
        task_name="topology_auditor.audit",
        context=context,
        require_tools=True,
        partition_hint=partition,
    )
    telemetry_result, topology_result = await asyncio.gather(
        telemetry_task, topology_task
    )
    telemetry = build_telemetry_report(partition, telemetry_result.executions)
    topology = build_topology_report(partition, topology_result.executions)
    golden_signals = analyze_golden_signals(telemetry, topology)
    verdict = await _synthesize_verdict(
        message=message,
        partition=partition,
        telemetry=telemetry,
        topology=topology,
        emit=emit,
        golden_signals=golden_signals,
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
    text = verdict.otto_response or verdict.summary
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


async def resolve_approval(approval_id: str, approved: bool) -> dict:
    """Execute or reject a pending mutation approval.

    Returns a small payload used by the WebSocket layer to stream the result.
    """
    approval = _pending_approvals.pop(approval_id, None)
    if approval is None:
        return {
            "approved": False,
            "text": "This approval request has expired or was already handled.",
        }
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
