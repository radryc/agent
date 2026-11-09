"""Deterministic task guardrails for the Strata SRE flow.

These replace prompt-level nagging with checks the runtime actually enforces.
Each guardrail is a pure function so it can be unit-tested without an LLM.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

from app.agent.contracts import IncidentVerdict

_METRIC_VALUE_TERMS = re.compile(
    r"\b(?:cpu|memory|usage|utilization|latency|load|rate|p9[59]|error rate|"
    r"request rate|throughput|metric value|how much|how many)\b",
    re.IGNORECASE,
)
_METRIC_VALUE_INTENT = re.compile(
    r"\b(?:show|give|what(?:'s| is)|current|value|values|how much|how many|"
    r"measure|query|check)\b",
    re.IGNORECASE,
)

_CATALOG_TOOLS = {"doctor_metrics_names", "doctor_metrics_labels"}


def metric_value_requested(user_message: str) -> bool:
    """Return True when the user is asking for a live metric value."""
    text = user_message or ""
    return bool(_METRIC_VALUE_TERMS.search(text) and _METRIC_VALUE_INTENT.search(text))


def metric_catalog_followup_required(
    user_message: str, called_tools: Iterable[str]
) -> bool:
    """A catalog lookup does not answer a value question.

    Returns True when the turn asked for a metric value, the model queried the
    metric catalog, and it never queried live values.
    """
    if not metric_value_requested(user_message):
        return False
    called = set(called_tools)
    if "doctor_metrics_query" in called:
        return False
    return bool(called & _CATALOG_TOOLS)


def _loads(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def tool_result_is_empty(result: str) -> bool:
    """Best-effort detection of an empty Doctor/Guardian/MonoFS result."""
    text = (result or "").strip()
    if not text:
        return True
    payload = _loads(text)
    if not isinstance(payload, dict):
        # Common plain-text markers from tool error paths.
        lowered = text.lower()
        return lowered.startswith("error") and "returned" not in lowered
    if payload.get("returned") == 0:
        return True
    for key in (
        "results",
        "traces",
        "spans",
        "services",
        "partitions",
        "log_groups",
        "metric_names",
        "lines",
    ):
        if isinstance(payload.get(key), list) and not payload[key]:
            return True
    return False


def zero_fabrication_ok(output: str, results: Iterable[str]) -> tuple[bool, str]:
    """Check that an agent did not invent data when every tool came back empty.

    Returns ``(ok, reason)``. A non-empty tool result always passes: the
    fabrication risk is specifically answering from nothing.
    """
    result_list = list(results)
    if not result_list or not all(tool_result_is_empty(item) for item in result_list):
        return True, ""
    text = (output or "").strip()
    if not text:
        return True, ""
    lowered = text.lower()
    empty_markers = (
        "no data",
        "no results",
        "empty",
        "returned nothing",
        "no logs",
        "no traces",
        "no metrics",
        "not found",
        "unavailable",
        "could not",
        "error",
    )
    if any(marker in lowered for marker in empty_markers):
        return True, ""
    return (
        False,
        "Every tool result was empty, but the answer did not report that. "
        "State that no data was returned instead of describing telemetry.",
    )


def apply_nightwatch_policy(
    verdict: IncidentVerdict,
    alerting_partition: str,
    *,
    escalation_confidence: float = 0.5,
) -> IncidentVerdict:
    """Apply Nightwatch safety rules to an incident verdict.

    * A root cause outside the alerting partition must be handed off, never
      acted on across a partition boundary.
    * Low confidence must escalate rather than mutate.
    """
    partition = str(alerting_partition or "").strip()
    root_partition = str(verdict.root_cause_partition or "").strip()

    if partition and root_partition and root_partition != partition:
        verdict.cross_partition = True
        verdict.escalate = True
        verdict.recommended_action = "handoff"
        verdict.recommendation_target = root_partition
        verdict.escalation_target = root_partition
        if not verdict.escalation_reason:
            verdict.escalation_reason = (
                "root-cause candidate is outside the alerting partition"
            )

    if verdict.confidence < escalation_confidence:
        verdict.escalate = True
        if verdict.recommended_action not in {"handoff"}:
            verdict.recommended_action = "escalate"
        if not verdict.escalation_reason:
            verdict.escalation_reason = (
                f"confidence {verdict.confidence:.2f} is below "
                f"{escalation_confidence:.2f}"
            )
    return verdict
