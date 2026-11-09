"""Build typed reports from raw tool executions.

The small local models are good at choosing tools and bad at producing
reliable JSON. Instead of trusting the model's self-report, these builders
derive the structured contracts from the actual tool outputs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.agent.contracts import (
    EvidenceRecord,
    ObservabilityGap,
    SteveProposalReport,
    TelemetryReport,
    TopologyReport,
)
from app.agent.guardrails import tool_result_is_empty

_ERROR_RE = re.compile(r"\b(error|exception|fail(?:ed|ure)?|panic|fatal|timeout|5\d\d)\b", re.I)
_ANOMALY_RE = re.compile(r"(error|fail|5\d\d|latency|timeout|usage|memory|cpu|oom)", re.I)
_MUTATING_WRITE_TOOLS = {"workspace_write", "workspace_commit"}


@dataclass
class ToolExecution:
    name: str
    args: dict = field(default_factory=dict)
    result: str = ""
    empty: bool = False

    def __post_init__(self) -> None:
        if not self.empty:
            self.empty = tool_result_is_empty(self.result)


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _short(value: Any, limit: int = 300) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, default=str)
    else:
        text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _walk(obj: Any, keys: set[str], out: list[str], limit: int = 8) -> None:
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                if isinstance(value, list):
                    for item in value:
                        text = _short(item, 160)
                        if text and text not in out:
                            out.append(text)
                        if len(out) >= limit:
                            return
                elif value not in (None, "", [], {}):
                    text = _short(value, 160)
                    if text not in out:
                        out.append(text)
            _walk(value, keys, out, limit)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, keys, out, limit)


def _collect_strings(payload: Any, keys: set[str], limit: int = 8) -> list[str]:
    out: list[str] = []
    _walk(payload, keys, out, limit)
    return out


def _iter_records(payload: Any) -> Iterable[Any]:
    if isinstance(payload, list):
        yield from payload
        return
    if not isinstance(payload, dict):
        return
    for key in ("results", "lines", "records", "entries", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            yield from value
    data = payload.get("data")
    if isinstance(data, dict):
        yield from _iter_records(data)


def _record_text(record: Any) -> str:
    if isinstance(record, str):
        return record
    if not isinstance(record, dict):
        return _short(record, 240)
    for key in ("line", "message", "msg", "text", "log", "body"):
        if record.get(key):
            return str(record[key])
    return _short(record, 240)


def _record_level(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
    return str(
        record.get("level")
        or record.get("severity")
        or labels.get("level")
        or labels.get("severity_text")
        or ""
    ).lower()


def _log_highlights(execution: ToolExecution) -> list[str]:
    payload = _loads(execution.result)
    highlights: list[str] = []
    for record in _iter_records(payload):
        text = _record_text(record)
        level = _record_level(record)
        if not text:
            continue
        if level in {"error", "fatal", "panic", "warn", "warning"} or _ERROR_RE.search(text):
            entry = _short(text, 240)
            if entry not in highlights:
                highlights.append(entry)
        if len(highlights) >= 5:
            break
    if highlights or payload is not None:
        return highlights
    for line in (execution.result or "").splitlines():
        if _ERROR_RE.search(line):
            entry = _short(line, 240)
            if entry not in highlights:
                highlights.append(entry)
        if len(highlights) >= 5:
            break
    return highlights


def _span_failures(execution: ToolExecution) -> list[str]:
    payload = _loads(execution.result)
    failures: list[str] = []
    if isinstance(payload, dict):
        summary = payload.get("error_summary")
        if isinstance(summary, dict) and int(summary.get("traces_with_errors") or 0) > 0:
            failures.append(
                f"{summary.get('traces_with_errors')} traces / "
                f"{summary.get('error_spans')} error spans"
            )
        traces = payload.get("traces")
        if isinstance(traces, list):
            for trace in traces:
                if not isinstance(trace, dict) or not trace.get("error_count"):
                    continue
                names = [
                    str(item.get("name"))
                    for item in (trace.get("error_spans") or [])
                    if isinstance(item, dict) and item.get("name")
                ]
                label = str(trace.get("trace_id") or trace.get("root_service") or "trace")
                entry = f"{label}: {', '.join(names[:3]) or 'error spans'}"
                if entry not in failures:
                    failures.append(entry)
                if len(failures) >= 5:
                    break
        spans = payload.get("spans")
        if isinstance(spans, list):
            for span in spans:
                if not isinstance(span, dict):
                    continue
                code = str(span.get("status_code") or "").upper()
                if "ERROR" in code:
                    entry = f"{span.get('service') or 'unknown'}/{span.get('name') or 'span'}: {code}"
                    if entry not in failures:
                        failures.append(entry)
                if len(failures) >= 5:
                    break
    return failures


def _metric_has_data(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return bool(payload)
    if int(payload.get("returned") or 0) > 0 or int(payload.get("series") or 0) > 0:
        return True
    for key in ("results", "result", "data"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value.get("result"):
            return True
    return False


def _metric_label(execution: ToolExecution) -> str:
    promql = str(execution.args.get("promql") or "").strip()
    if promql:
        return promql
    payload = _loads(execution.result)
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            labels = results[0].get("labels")
            if isinstance(labels, dict) and labels:
                return _short(labels, 120)
    return execution.name


def _kind_for(name: str) -> str:
    if name.startswith("doctor_metrics") or "metric" in name:
        return "metric"
    if name.startswith("doctor_log") or "log" in name:
        return "log"
    if "trace" in name:
        return "trace"
    if name.startswith("workspace_") or name.startswith("monofs_"):
        return "code"
    return "topology"


def _source_for(name: str) -> str:
    if name.startswith("guardian") or name.startswith("lb_edge"):
        return "guardian"
    if name.startswith("monofs") or name.startswith("workspace"):
        return "monofs"
    return "doctor"


def _evidence(execution: ToolExecution) -> EvidenceRecord:
    return EvidenceRecord(
        kind=_kind_for(execution.name),  # type: ignore[arg-type]
        source=_source_for(execution.name),  # type: ignore[arg-type]
        query=f"{execution.name}({_short(execution.args, 180)})",
        result_summary=_short(execution.result, 400) or "(empty)",
        raw_hash=_hash(execution.result),
        empty=execution.empty,
    )


def _failure_count(payload: Any) -> int:
    total = 0
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("failing", "failed", "unhealthy")):
                if isinstance(value, bool):
                    total += int(value)
                elif isinstance(value, (int, float)):
                    total += int(value)
                elif isinstance(value, list):
                    total += len(value)
            total += _failure_count(value)
    elif isinstance(payload, list):
        for item in payload[:50]:
            total += _failure_count(item)
    return total


def _topology_health(executions: list[ToolExecution]) -> str:
    for execution in executions:
        payload = _loads(execution.result)
        if isinstance(payload, dict):
            for key in ("health", "status", "overall_health"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    lowered = value.lower()
                    if lowered in {"healthy", "ok", "up", "green"}:
                        return "healthy"
                    if lowered in {"degraded", "warning", "warn", "yellow"}:
                        return "degraded"
                    if lowered in {"failing", "failed", "red", "down"}:
                        return "failing"
            if _failure_count(payload) > 0:
                return "degraded"
        elif execution.name.startswith("guardian") and any(
            word in (execution.result or "").lower()
            for word in ("failing", "crashloop", "degraded")
        ):
            return "degraded"
    return "healthy" if any(not item.empty for item in executions) else "unknown"


def build_telemetry_report(
    partition: str, executions: list[ToolExecution]
) -> TelemetryReport:
    report = TelemetryReport(partition=partition)
    for execution in executions:
        kind = _kind_for(execution.name)
        payload = _loads(execution.result)
        if execution.empty:
            report.empty_signals.append(execution.name)
        if kind == "metric":
            label = _metric_label(execution)
            if label and label not in report.metrics_queried:
                report.metrics_queried.append(label)
            if _metric_has_data(payload) and _ANOMALY_RE.search(label):
                if label not in report.anomalous_metrics:
                    report.anomalous_metrics.append(label)
        elif kind == "log":
            for line in _log_highlights(execution):
                if line not in report.error_logs:
                    report.error_logs.append(line)
        elif kind == "trace":
            for failure in _span_failures(execution):
                if failure not in report.failing_spans:
                    report.failing_spans.append(failure)
        report.evidence.append(_evidence(execution))

    for signal in report.empty_signals:
        kind = _kind_for(signal)
        category = {
            "metric": "metrics",
            "log": "logs",
            "trace": "traces",
            "code": "docs",
        }.get(kind, "labels")
        report.observability_gaps.append(
            ObservabilityGap(
                title=f"No {category} data returned for {partition or 'the partition'}",
                category=category,  # type: ignore[arg-type]
                rationale=f"{signal} returned an empty result during this investigation.",
                proposed_change=(
                    f"Verify the {category} pipeline and labels for "
                    f"{partition or 'this partition'} so future investigations have evidence."
                ),
                target=partition or signal,
                confidence=0.4,
            )
        )
    return report


def build_topology_report(
    partition: str, executions: list[ToolExecution]
) -> TopologyReport:
    report = TopologyReport(partition=partition, health_status=_topology_health(executions))
    for execution in executions:
        payload = _loads(execution.result)
        for value in _collect_strings(payload, {"drifted_intents", "driftedIntents", "drifted"}):
            if value not in report.drifted_intents:
                report.drifted_intents.append(value)
        for value in _collect_strings(
            payload, {"failing_assets", "failingAssets", "unhealthy_assets", "unhealthyAssets"}
        ):
            if value not in report.failing_assets:
                report.failing_assets.append(value)
        for value in _collect_strings(
            payload, {"cross_partition_targets", "crossPartitionTargets", "target_partition"}
        ):
            if value not in report.cross_partition_targets:
                report.cross_partition_targets.append(value)
        if execution.name in {
            "guardian_intent_activity",
            "doctor_event_analysis",
            "doctor_guardian_events",
        }:
            entry = f"{execution.name}: {_short(execution.result, 240)}"
            if not execution.empty:
                report.recent_events.append(entry)
        report.evidence.append(_evidence(execution))
    return report


def build_steve_report(
    executions: list[ToolExecution],
    summary: str,
    *,
    unified_diff: str = "",
    validation_result: str = "",
    verification_command: str = "",
) -> SteveProposalReport:
    files: list[str] = []
    for execution in executions:
        if execution.name in _MUTATING_WRITE_TOOLS:
            path = str(execution.args.get("path") or "").strip()
            if path and path not in files:
                files.append(path)
    for match in re.finditer(r"^\+\+\+ b/(.+)$", unified_diff or "", re.MULTILINE):
        path = match.group(1).strip()
        if path and path not in files:
            files.append(path)
    return SteveProposalReport(
        files_changed=files,
        unified_diff=unified_diff or "",
        validation_result=validation_result,
        verification_command=verification_command,
        summary=summary,
    )
