"""Four Golden Signals observability readiness.

The flow runs this as the final diagnostic step: after collecting evidence,
check whether the investigation actually covered latency, traffic, errors, and
saturation. Missing coverage is itself an observability gap worth reporting.
"""

from __future__ import annotations

import re

from app.agent.contracts import (
    GoldenSignalCoverage,
    ObservabilityGap,
    TelemetryReport,
    TopologyReport,
)

SIGNALS = ("latency", "traffic", "errors", "saturation")

_PATTERNS = {
    "latency": re.compile(
        r"latency|duration|response_time|p9[59]|histogram|ttfb|slow", re.I
    ),
    "traffic": re.compile(
        r"traffic|throughput|qps|rps|request_rate|requests_total|http_requests|connections",
        re.I,
    ),
    "errors": re.compile(
        r"error|fail|5\d\d|5xx|5\.\.|exception|status.*code|availability", re.I
    ),
    "saturation": re.compile(
        r"cpu|memory|usage|utilization|saturation|queue|disk|filesystem|load|oom|"
        r"inflight|in_flight|backlog",
        re.I,
    ),
}

_RECOMMENDED = {
    "latency": (
        "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))"
    ),
    "traffic": "sum(rate(http_requests_total[5m]))",
    "errors": 'sum(rate(http_requests_total{status=~"5.."}[5m]))',
    "saturation": "max(container_memory_usage_bytes) by (pod)",
}

_PURPOSE = {
    "latency": "How long requests take (p95/p99, histograms, or trace durations).",
    "traffic": "How much demand the system receives (request rate, throughput, QPS).",
    "errors": "How often requests fail (5xx rate, error logs, failing spans).",
    "saturation": "How full the resources are (CPU, memory, queues, disk, load).",
}


def _metric_queries(telemetry: TelemetryReport) -> list[tuple[str, bool]]:
    """Return (query text, returned_data) for metric evidence records."""
    out: list[tuple[str, bool]] = []
    for record in telemetry.evidence:
        if record.kind != "metric":
            continue
        query = record.query
        text = query
        for pattern in (
            r'"promql"\s*:\s*"((?:[^"\\]|\\.)*)"',
            r"'promql'\s*:\s*'([^']*)'",
        ):
            match = re.search(pattern, query)
            if match:
                text = match.group(1).replace('\\"', '"')
                break
        out.append((text, not record.empty))
    return out


def _has_metric(metrics: list[tuple[str, bool]], pattern: re.Pattern) -> list[str]:
    return [
        query
        for query, has_data in metrics
        if has_data and pattern.search(query)
    ]


def analyze_golden_signals(
    telemetry: TelemetryReport,
    topology: TopologyReport | None = None,
) -> list[GoldenSignalCoverage]:
    """Assess evidence coverage for the four golden signals."""
    metrics = _metric_queries(telemetry)
    coverage: list[GoldenSignalCoverage] = []

    latency_metric = _has_metric(metrics, _PATTERNS["latency"])
    trace_evidence = [
        record.query
        for record in telemetry.evidence
        if record.kind == "trace" and not record.empty
    ]
    latency_evidence = latency_metric + trace_evidence
    coverage.append(
        GoldenSignalCoverage(
            signal="latency",
            covered=bool(latency_evidence),
            evidence=latency_evidence[:4],
            gap=(
                ""
                if latency_evidence
                else "No latency metric or trace duration evidence was collected."
            ),
            recommended_query=_RECOMMENDED["latency"],
        )
    )

    traffic = _has_metric(metrics, _PATTERNS["traffic"])
    coverage.append(
        GoldenSignalCoverage(
            signal="traffic",
            covered=bool(traffic),
            evidence=traffic[:4],
            gap=(
                ""
                if traffic
                else "No request-rate or throughput metric was queried."
            ),
            recommended_query=_RECOMMENDED["traffic"],
        )
    )

    error_metrics = _has_metric(metrics, _PATTERNS["errors"])
    error_evidence = error_metrics + telemetry.error_logs[:2] + telemetry.failing_spans[:2]
    coverage.append(
        GoldenSignalCoverage(
            signal="errors",
            covered=bool(error_evidence),
            evidence=error_evidence[:4],
            gap=(
                ""
                if error_evidence
                else "No error metrics, error logs, or failing spans were found."
            ),
            recommended_query=_RECOMMENDED["errors"],
        )
    )

    saturation = _has_metric(metrics, _PATTERNS["saturation"])
    coverage.append(
        GoldenSignalCoverage(
            signal="saturation",
            covered=bool(saturation),
            evidence=saturation[:4],
            gap=(
                ""
                if saturation
                else "No CPU, memory, queue, disk, or load metric was queried."
            ),
            recommended_query=_RECOMMENDED["saturation"],
        )
    )
    return coverage


def gaps_from_coverage(
    coverage: list[GoldenSignalCoverage], partition: str
) -> list[ObservabilityGap]:
    """Turn uncovered golden signals into Nightwatch-style improvements."""
    gaps: list[ObservabilityGap] = []
    for item in coverage:
        if item.covered:
            continue
        gaps.append(
            ObservabilityGap(
                title=f"Golden signal not covered: {item.signal}",
                category="metrics",
                rationale=(
                    f"{_PURPOSE[item.signal]} The investigation did not collect "
                    "enough evidence for this signal."
                ),
                proposed_change=(
                    f"Add or verify a query for {item.signal}: "
                    f"`{item.recommended_query}`"
                ),
                target=partition or "platform",
                confidence=0.55,
            )
        )
    return gaps


def format_golden_signals(
    coverage: list[GoldenSignalCoverage],
) -> str:
    """Render a compact, operator-readable readiness section."""
    covered = sum(1 for item in coverage if item.covered)
    lines = [
        f"### Observability readiness — {covered}/{len(coverage)} golden signals covered"
    ]
    for item in coverage:
        if item.covered:
            detail = "; ".join(item.evidence[:2]) or "evidence collected"
            lines.append(f"- **{item.signal.title()}**: covered — {detail}")
        else:
            lines.append(
                f"- **{item.signal.title()}**: **gap** — {item.gap} "
                f"Suggested query: `{item.recommended_query}`"
            )
    if covered < len(coverage):
        lines.append(
            "\nThe gaps above are observability work, not application fixes. "
            "They can be handed to Software Steve as a proposal."
        )
    return "\n".join(lines)
