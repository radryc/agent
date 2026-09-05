from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

from app.config import settings, get_runtime
from app.agent import session as session_mgr
from app.agent.tools import execute_tool, _TOOL_INSTRUCTIONS, skill_list, get_skill
from app.llm.client import chat_completion

logger = logging.getLogger(__name__)

# Matches a bare JSON tool-call line: {"tool": "...", "args": {...}}
_TOOL_RE = re.compile(r'^\s*\{"tool"\s*:\s*".+?".*\}\s*$', re.MULTILINE)

MAX_TOOL_ROUNDS = 5
_PROPOSAL_FIRST_APPENDIX = (
    "\n\nOperating policy:\n"
    "- Be a comprehensive Strata developer helper across Doctor telemetry, MonoFS development, and Guardian deployments.\n"
    "- For any change that mutates state (deploy/reconcile/install), do proposal-first: diagnose, propose concrete steps, and execute only after approval.\n"
    "- Prefer doctor_event_analysis + guardian detail for incident triage, monofs_search for code localization, and deploy tools only after an approved plan.\n"
)

_HELP_TEXT = (
    "You are Strata developer helper. Agent mode uses structured tools; terminal mode gives full bash.\n\n"
    "=== SLASH COMMANDS ===\n"
    "/help       — This reference.\n"
    "/mode [agent|terminal] — Switch between agent and terminal mode. Ctrl+E toggles.\n\n"
    "=== HOW TO USE TOOLS ===\n"
    "Output a single JSON line — nothing else:\n"
    '  {"tool": "guardian_overview", "args": {}}\n'
    '  {"tool": "monofs_search", "args": {"query": "HandleRequest"}}\n'
    '  {"tool": "doctor_event_analysis", "args": {"partition": "monofs"}}\n'
    "\n"
    "Mutation safety:\n"
    "- Mutating tools require explicit approval and `confirm=true` in args.\n"
    "- Default workflow is proposal-first: diagnose -> propose -> execute after approval.\n"
    "The result comes back, then you produce your final answer.\n\n"

    "--- STRATA WORKFLOW ---\n"
    "strata_stack_check(timeout_seconds: int=8)\n"
    "  Verifies Doctor/Guardian/MonoFS/guardianctl reachability before work.\n\n"
    "remote_vscode_plan(method: auto|ssh|code_tunnel|code-server, host: str,\n"
    "                   user: str, port: int=8080, workspace_dir: str)\n"
    "  Generates secure remote VS Code installation/access plan.\n\n"
    "remote_vscode_install(method: code-server|ssh, plan_only: bool=true)\n"
    "  Installs remote VS Code dependencies. MUTATING tool — plan-only by default,\n"
    "  requires confirm=true when plan_only=false.\n\n"
    "--- GUARDIAN (deployment orchestration) ---\n"
    "guardian_overview()\n"
    "  Dashboard: all partitions, health buckets, asset counts.\n"
    "  Start here when someone says \"is everything up?\".\n\n"
    "guardian_partitions()\n"
    "  List partition names + metadata. Lighter than overview.\n\n"
    "guardian_partition_detail(partition: str)\n"
    "  Full dump: manifest, runtime state, every intent with asset health,\n"
    "  topology graph, recent events. Use for surgical diagnostics.\n"
    "  Known partitions: agent monofs doctor monitoring opentelemetry\n"
    "  k8s-top aws-demo lb-edge lb-agent _bootstrap guardian-configs\n\n"
    "guardian_intent_activity(partition: str, intent: str)\n"
    "  Per-intent: status, drift report, observed health, last-error,\n"
    "  apply readiness, timestamps. What you need when an intent is red.\n\n"
    "guardian_reconcile(partition: str)\n"
    "  Force immediate reconciliation. MUTATING tool — requires confirm=true.\n\n"
    "guardian_deploy_partition(partition_dir: str, ..., plan_only: bool=true)\n"
    "  Deploy partition with guardianctl (push/reconcile/wait). Default is plan-only;\n"
    "  set plan_only=false and confirm=true only after approval.\n\n"
    "guardian_catalog()\n"
    "  14 asset types (Compute, Volume, LoadBalancer, ImageBuild, Config,\n"
    "  Secret, Network, ObjectStore, SQLDatabase, CDKStack, TraefikRoute,\n"
    "  Observability, Database) and available target pushers.\n\n"
    "--- DOCTOR (logs / traces / metrics) ---\n"
    "doctor_logs_services()\n"
    "  Which services have log data. Quick existence check.\n\n"
    "doctor_logs_search(service: str?, query: str?, limit: int=20)\n"
    "  Full-text log search. Tip: pass the Compute asset name as service.\n\n"
    "doctor_metrics_query(promql: str, start: str?, end: str?, step: str=15s)\n"
    "  PromQL: 'rate(http_requests_total[5m])', 'container_memory_usage_bytes',\n"
    "  'up'. Use start/end for range; omit for instant value.\n\n"
    "doctor_traces_recent(service: str?, limit: int=20)\n"
    "  Trace summaries: trace_id, root span, duration. Find slow paths.\n\n"
    "doctor_trace_detail(trace_id: str)\n"
    "  Every span in a trace: timings, status codes, attributes.\n\n"
    "doctor_guardian_events(partition: str?, limit: int=20)\n"
    "  Deployment timeline: reconcile cycles, task completions, failures.\n\n"
    "doctor_event_analysis(partition: str?, tenant: str='default', limit: int=80)\n"
    "  Summarizes Guardian events: failures/successes, impacted partitions,\n"
    "  and prioritized failure signals for root-cause analysis.\n\n"
    "--- MONOFS (code search / filesystem) ---\n"
    "monofs_search(query: str, max_results: int=20, file_pattern: str?,\n"
    "              case_sensitive: bool?, regex: bool?)\n"
    "  Full-text search across ALL ingested repos. Finds functions, configs,\n"
    "  error strings, imports. Your grep-on-steroids for the whole codebase.\n"
    "  Examples:\n"
    '    {"tool":"monofs_search","args":{"query":"func Handle"}}\n'
    '    {"tool":"monofs_search","args":{"query":"TODO","max_results":"50"}}\n'
    '    {"tool":"monofs_search","args":{"query":"intent","file_pattern":"*.yaml"}}\n'
    '    {"tool":"monofs_search","args":{"query":"rate_limit","regex":"true"}}\n\n'
    "monofs_status()\n"
    "  Local workspace: pending changes, staged files, unpushed commits.\n\n"
    "monofs_refs()\n"
    "  All ingested repos + commit hashes. Check if you're behind upstream.\n\n"
    "--- GENERAL ---\n"
    "datetime_now()              — Current UTC time.\n"
    "calculate(expression: str)   — Math: 'sqrt(144)', '2**10'.\n"
    "web_search(query: str, max_results: int=5) — DuckDuckGo web search.\n"
    "web_fetch(url: str, max_chars: int=6000)   — Scrape + extract text.\n"
    "generate_image(prompt: str, ...)           — Gemini Imagen.\n"
    "generate_image_gemini(...)                 — Explicit Gemini alias.\n\n"
    "=== SRE WORKFLOW ===\n"
    "When diagnosing issues, follow the checklist:\n"
    "  1. guardian_overview()          — Spot red partitions.\n"
    "  2. guardian_partition_detail()  — Zoom into the sick one.\n"
    "  3. guardian_intent_activity()   — Drift? Health? Last error?\n"
    "  4. doctor_logs_search()         — What was the service saying?\n"
    "  5. doctor_metrics_query()       — CPU/memory/latency trending?\n"
    "  6. doctor_traces_recent()       — Which span is the bottleneck?\n"
    "  7. monofs_search()              — Find the offending code.\n"
    "  8. guardian_reconcile()         — Fix it (if you dare).\n"
    "Use tools first, then answer with evidence and a clear next action."
)

# Always appended to the system prompt so tools work even with custom prompts
# Auto-generated from the skill registry in tools.py


def _tool_status_text(tool_name: str, tool_args: dict) -> str:
    skill = get_skill(tool_name)
    if skill and skill.status_prefix:
        return skill.status_prefix + "..."
    if tool_name == "web_search":
        return f"Searching the web: {tool_args.get('query', tool_name)}..."
    if tool_name == "web_fetch":
        return f"Fetching page: {tool_args.get('url', tool_name)}..."
    if tool_name in ("generate_image", "generate_image_gemini"):
        return "Generating image with Gemini..."
    return f"Running tool: {tool_name}..."


def _extract_tool_call(text: str) -> dict | None:
    """Return parsed tool call dict if the model output contains one, else None."""
    match = _TOOL_RE.search(text)
    if not match:
        return None
    try:
        obj = json.loads(match.group())
        if "tool" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


async def _stream_text_once(text: str) -> AsyncIterator[str]:
    yield text


async def run_agent_turn(
    user_message: str,
    session_id: str,
    *,
    stream: bool = True,
) -> AsyncIterator[str] | str:
    if user_message.strip().lower() == "/help":
        await session_mgr.add_message(session_id, "user", user_message)
        await session_mgr.add_message(session_id, "assistant", _HELP_TEXT)
        if not stream:
            return _HELP_TEXT
        return _stream_text_once(_HELP_TEXT)

    history = await session_mgr.get_messages(session_id)

    system_prompt = get_runtime("system_prompt") or settings.agent_system_prompt
    # Always inject tool instructions, even if user customized the system prompt
    full_system_prompt = system_prompt + _PROPOSAL_FIRST_APPENDIX + _TOOL_INSTRUCTIONS
    logger.info("[LOOP] Starting turn. System prompt (first 200): %.200s", system_prompt)
    messages = [{"role": "system", "content": full_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    await session_mgr.add_message(session_id, "user", user_message)

    if not stream:
        result = await _run_tool_loop(messages)
        await session_mgr.add_message(session_id, "assistant", result)
        return result

    return _stream_tool_loop(messages, session_id)


async def _run_tool_loop(messages: list[dict]) -> str:
    """Non-streaming: run up to MAX_TOOL_ROUNDS of tool calls then return final answer."""
    for _ in range(MAX_TOOL_ROUNDS):
        response = await chat_completion(messages, stream=False)
        logger.info("[LOOP] LLM raw response (non-stream): %.500s", response)
        tool_call = _extract_tool_call(response)
        if not tool_call:
            logger.info("[LOOP] No tool call detected, returning response.")
            return response

        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})
        logger.info("Tool call: %s %s", tool_name, tool_args)

        tool_result = await execute_tool(tool_name, tool_args)
        logger.info("Tool result: %.200s", tool_result)

        # Feed result back as a user message so the model can use it
        messages.append({"role": "assistant", "content": response})
        messages.append({
            "role": "user",
            "content": f"[Tool result for {tool_name}]\n{tool_result}\n\nNow answer based on this."
        })

    # Fallback: final completion after max rounds
    return await chat_completion(messages, stream=False)


async def _stream_tool_loop(
    messages: list[dict], session_id: str
) -> AsyncIterator[str]:
    """Streaming: detect tool calls, execute silently, then stream the final answer."""
    working_messages = list(messages)

    for round_num in range(MAX_TOOL_ROUNDS):
        # Collect full response first to check for tool calls
        collected: list[str] = []
        token_stream = await chat_completion(working_messages, stream=True)
        async for token in token_stream:
            collected.append(token)
        response = "".join(collected)
        logger.info("[LOOP] LLM raw response (stream round %d): %.500s", round_num, response)

        tool_call = _extract_tool_call(response)
        if not tool_call:
            logger.info("[LOOP] No tool call detected in round %d, streaming final answer.", round_num)
            # No tool call — stream the final answer token by token
            for token in collected:
                yield token
            if response:
                await session_mgr.add_message(session_id, "assistant", response)
            return

        # Execute tool silently (no streaming to client yet)
        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})
        logger.info("Tool call (round %d): %s %s", round_num, tool_name, tool_args)

        # Yield a status indicator so the UI shows something is happening
        yield f"\n\n*{_tool_status_text(tool_name, tool_args)}*\n\n"

        tool_result = await execute_tool(tool_name, tool_args)
        logger.info("Tool result: %.200s", tool_result)

        working_messages.append({"role": "assistant", "content": response})
        working_messages.append({
            "role": "user",
            "content": f"[Tool result for {tool_name}]\n{tool_result}\n\nNow answer based on this."
        })

    # Max rounds exhausted — do one final non-streaming call
    final = await chat_completion(working_messages, stream=False)
    yield final
    if final:
        await session_mgr.add_message(session_id, "assistant", final)


async def ensure_session(session_id: str | None) -> str:
    if session_id:
        existing = await session_mgr.get_session(session_id)
        if existing:
            return session_id

    result = await session_mgr.create_session()
    return result["id"]
