"""Scoped specialist runners for the Strata SRE flow.

Each specialist is a small tool loop with an explicit tool allowlist. The
runner deliberately reuses the proven completion/grammar/hybrid helpers from
``app.agent.loop`` while avoiding the monolithic 53-tool prompt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.agent import loop as loop_mod
from app.agent.guardrails import metric_catalog_followup_required, zero_fabrication_ok
from app.agent.reports import ToolExecution
from app.agent.tools import build_tool_instructions, execute_tool
from app.llm.client import max_tool_rounds

logger = logging.getLogger(__name__)

EventSink = Callable[[dict], Awaitable[None]]

AGENT_NAMES = {
    "telemetry_hound": "TelemetryHound",
    "topology_auditor": "TopologyAuditor",
    "software_steve": "SoftwareSteve",
    "outage_otto": "OutageOtto",
}

_TOOL_NUDGE = (
    "You did not call a tool. This task requires live evidence. Emit one JSON "
    'tool call per line, for example: {"tool": "doctor_logs_search", '
    '"args": {"partition": "monofs", "limit": 20}}. Do not answer from memory.'
)


@dataclass
class SpecialistResult:
    agent_id: str
    text: str
    executions: list[ToolExecution] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    profile: str = "local"

    @property
    def called_tools(self) -> list[str]:
        return [item.name for item in self.executions]

    @property
    def tool_results(self) -> list[str]:
        return [item.result for item in self.executions]


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


async def emit_step(
    emit: EventSink | None,
    agent_id: str,
    task_name: str,
    status: str,
    *,
    tool: str | None = None,
    tool_args: dict | None = None,
    summary: str | None = None,
) -> None:
    if emit is None:
        return
    payload = {
        "type": "crew_step",
        "agent_id": agent_id,
        "agent_name": AGENT_NAMES.get(agent_id, agent_id),
        "task_name": task_name,
        "status": status,
        "ts": _ts(),
    }
    if tool:
        payload["tool"] = tool
    if tool_args is not None:
        payload["tool_args"] = tool_args
    if summary:
        payload["summary"] = summary[-600:]
    try:
        await emit(payload)
    except Exception:  # pragma: no cover - a dead UI socket must not kill the turn
        logger.debug("Failed to emit crew step", exc_info=True)


def _first_metric_name(executions: list[ToolExecution]) -> str:
    for execution in executions:
        if execution.name not in {"doctor_metrics_names", "doctor_metrics_labels"}:
            continue
        try:
            payload = json.loads(execution.result)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        names = payload.get("metric_names") or payload.get("labels")
        if isinstance(payload.get("metric_names"), list) and payload["metric_names"]:
            return str(payload["metric_names"][0])
        if isinstance(names, list) and names:
            return f'{{__name__=~"{names[0]}.*"}}'
    return ""


def _build_messages(
    system_prompt: str,
    allowed: set[str] | None,
    user_message: str,
    context: str,
) -> list[dict]:
    system = system_prompt + build_tool_instructions(allowed)
    if context.strip():
        system += "\n\nConversation context:\n" + context.strip()[-4000:]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


async def run_specialist(
    *,
    agent_id: str,
    system_prompt: str,
    allowed: set[str] | None,
    user_message: str,
    profile: str = "local",
    emit: EventSink | None = None,
    task_name: str = "",
    context: str = "",
    require_tools: bool = True,
    metric_value: bool = False,
    partition_hint: str = "",
    max_rounds: int | None = None,
) -> SpecialistResult:
    """Run one scoped tool loop and return its evidence package."""
    task_name = task_name or f"{agent_id}.collect"
    result = SpecialistResult(agent_id=agent_id, text="", profile=profile)
    await emit_step(emit, agent_id, task_name, "started")
    executions: list[ToolExecution] = result.executions
    messages = _build_messages(system_prompt, allowed, user_message, context)

    active_profile = profile
    escalated = False
    force_grammar = False
    used_tools = False
    nudged = False
    metric_followed = False
    rounds = max_rounds or min(4, max_tool_rounds())

    try:
        for round_num in range(rounds):
            if (
                not escalated
                and active_profile == "local"
                and loop_mod._should_escalate(profile, round_num)
            ):
                active_profile = "fallback"
                escalated = True
                logger.info(
                    "[%s] Escalating to fallback after %d rounds", agent_id, round_num
                )

            response = await loop_mod._model_round(
                messages, active_profile, force_grammar=force_grammar
            )
            force_grammar = False
            calls = loop_mod._extract_tool_calls(response)

            if not calls:
                if (
                    require_tools
                    and not used_tools
                    and not nudged
                    and round_num + 1 < rounds
                ):
                    messages.append({"role": "system", "content": _TOOL_NUDGE})
                    nudged = True
                    force_grammar = True
                    continue
                result.text = loop_mod._strip_tool_calls(response)
                break

            used_tools = True
            assistant_text = loop_mod._strip_tool_calls(response)
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})

            formatted: list[tuple[str, str]] = []
            for call in calls:
                name = str(call.get("tool", ""))
                args = call.get("args", {})
                if not isinstance(args, dict):
                    args = {}
                await emit_step(
                    emit,
                    agent_id,
                    task_name,
                    "tool_call",
                    tool=name,
                    tool_args=args,
                    summary=f"{name}",
                )
                output = await execute_tool(name, args, allowed=allowed)
                execution = ToolExecution(name=name, args=args, result=output)
                executions.append(execution)
                formatted.append((name, output))
                await emit_step(
                    emit,
                    agent_id,
                    task_name,
                    "tool_result",
                    tool=name,
                    summary=output,
                )

            messages.append({"role": "user", "content": loop_mod._format_tool_results(formatted)})

            if (
                metric_value
                and not metric_followed
                and metric_catalog_followup_required(
                    user_message, [item.name for item in executions]
                )
            ):
                metric_followed = True
                metric_name = _first_metric_name(executions)
                if metric_name:
                    selector = metric_name
                    if partition_hint:
                        selector += f'{{guardian_partition="{partition_hint}"}}'
                    promql = f"topk(5, {selector})"
                    await emit_step(
                        emit,
                        agent_id,
                        task_name,
                        "tool_call",
                        tool="doctor_metrics_query",
                        tool_args={"promql": promql},
                        summary=promql,
                    )
                    auto_result = await execute_tool(
                        "doctor_metrics_query",
                        {"promql": promql, "compact": True},
                        allowed=allowed,
                    )
                    executions.append(
                        ToolExecution(
                            name="doctor_metrics_query",
                            args={"promql": promql, "automatic": True},
                            result=auto_result,
                        )
                    )
                    await emit_step(
                        emit,
                        agent_id,
                        task_name,
                        "tool_result",
                        tool="doctor_metrics_query",
                        summary=auto_result,
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[Automatic metric value query]\n"
                                f"PromQL: {promql}\n{auto_result}\n\n"
                                "The user asked for a value, so this query was run "
                                "automatically after the catalog lookup. Report the "
                                "real result; do not answer with only the catalog."
                            ),
                        }
                    )
                    continue

        else:
            result.text = await loop_mod.chat_completion(
                messages, stream=False, profile=active_profile
            )

        if not result.text:
            result.text = await loop_mod.chat_completion(
                messages, stream=False, profile=active_profile
            )

        ok, reason = zero_fabrication_ok(result.text, result.tool_results)
        if not ok:
            result.errors.append(reason)
            result.text = (
                "No data was returned by the tools, so there is nothing to "
                "report.\n\n" + result.text
            )
        result.profile = active_profile
        await emit_step(
            emit,
            agent_id,
            task_name,
            "completed",
            summary=result.text or "Completed with no narrative summary.",
        )
        return result
    except Exception as exc:  # pragma: no cover - model/network failure path
        logger.exception("%s specialist failed", agent_id)
        result.errors.append(str(exc))
        result.profile = active_profile
        await emit_step(emit, agent_id, task_name, "failed", summary=str(exc))
        return result
