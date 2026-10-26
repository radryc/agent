from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

from app.agent import session as session_mgr
from app.agent.personas import (
    DEFAULT_PERSONA_ID,
    allowed_tools,
    get_persona,
    persona_system_prompt,
)
from app.agent.tools import execute_tool, build_tool_instructions, get_skill
from app.llm.client import chat_completion

logger = logging.getLogger(__name__)

# Matches a bare JSON tool-call line: {"tool": "...", "args": {...}}
_TOOL_RE = re.compile(r'^\s*\{"tool"\s*:\s*".+?".*\}\s*$', re.MULTILINE)

# Infrastructure/telemetry requests must be answered from a tool result. The
# model is allowed to be terse, but it is not allowed to invent live state.
_TELEMETRY_TERMS = re.compile(
    r"\b("
    r"logs?|traces?|spans?|metrics?|telemetry|errors?|latency|p9[59]|"
    r"cpu|memory|partitions?|intents?|assets?|guardian|doctor|monofs|"
    r"health(?:y)?|status|deploy(?:ment)?|reconcile|drift(?:ed)?|incident|"
    r"failing|degraded|broken|"
    r"outage|slo|5xx|4xx|auth|sso|oidc"
    r")\b",
    re.IGNORECASE,
)

_METRIC_VALUE_TERMS = re.compile(
    r"\b(?:cpu|memory|usage|utilization|latency|load|rate|p9[59]|error rate|"
    r"request rate|throughput|metric value|how much|how many)\b",
    re.IGNORECASE,
)
_PARTITION_HINT_RE = re.compile(
    r"\b(agent|doctor|monofs|monitoring|opentelemetry|k8s-top|lb-agent|lb-edge|guardian-configs)\b",
    re.IGNORECASE,
)
_METRIC_VALUE_INTENT = re.compile(
    r"\b(?:show|give|what(?:'s| is)|current|value|values|how much|how many|"
    r"measure|query|check)\b",
    re.IGNORECASE,
)

_TOOL_USE_NUDGE = (
    "You did not call a tool. This request is about live infrastructure or "
    "telemetry, so you must call at least one relevant tool before answering. "
    'Emit one JSON tool call per line, for example:\n'
    '{"tool": "doctor_logs_search", "args": {"partition": "monofs", "limit": 20}}\n'
    "Do not answer from memory or from the previous turn's context."
)

_METRIC_QUERY_NUDGE = (
    "You listed metric names but did not query a value. The user asked for a "
    "metric value. Run doctor_metrics_query now with a real selector (use "
    "doctor_metrics_labels first if you need the exact label names). Do not "
    "answer the value question with a catalog listing."
)

_EVIDENCE_REMINDER = (
    "Answer only from the tool results above. If a result is empty, denied, or "
    "returned an error, say that explicitly. Never invent counts, service names, "
    "timestamps, latencies, log lines, trace IDs, or query results. The persona "
    "controls tone only; it must not add facts that are not in the results."
)

# DeepSeek (notably in streaming mode) sometimes emits its native DSML
# tool-call markup instead of the JSON line we ask for:
#   <｜｜DSML｜｜ calls>
#   <｜｜DSML｜｜ invoke name="guardian_overview">
#   </｜｜DSML｜｜ invoke>
#   </｜｜DSML｜｜ calls>
# Accept both the full-width bars and the ASCII "||" variant.
_DSML_BAR = r"[｜|]{2}"
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML_BAR}DSML{_DSML_BAR}\s*invoke\s+name=\"([^\"]+)\"\s*>(.*?)"
    rf"</{_DSML_BAR}DSML{_DSML_BAR}\s*invoke>",
    re.DOTALL | re.IGNORECASE,
)
_DSML_PARAM_RE = re.compile(
    rf"<{_DSML_BAR}DSML{_DSML_BAR}\s*parameter\s+name=\"([^\"]+)\"[^>]*>(.*?)"
    rf"</{_DSML_BAR}DSML{_DSML_BAR}\s*parameter>",
    re.DOTALL | re.IGNORECASE,
)

MAX_TOOL_ROUNDS = 5

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
    "guardian_asset_describe(type: str, partition: str?, intent: str?, asset: str?)\n"
    "  Template fields and hints for an asset type before authoring it.\n\n"
    "guardian_intent_plan(partition: str)\n"
    "  Compile a dry-run plan for a partition after manifest edits.\n\n"
    "guardian_partition_validate(root: str?='./stratatools/partitions')\n"
    "  Validate local partition/intent/asset bundles before proposing a release.\n\n"
    "--- DOCTOR (logs / traces / metrics) ---\n"
    "doctor_logs_services(partition: str?)\n"
    "  Native log inventory: services/partitions. log_groups is AWS-only.\n\n"
    "doctor_logs_search(partition: str?, service: str?, component: str?, asset: str?,\n"
    "                   query: str?, from: str?, to: str?, limit: int=20)\n"
    "  Log search. Use component=guardian component (monofs-router) or asset=guardian\n"
    "  asset (router-a) for short names; the tool over-fetches and filters locally.\n"
    "  Omit query to list recent logs.\n\n"
    "doctor_metrics_names(search: str?, limit: int=200)\n"
    "  Discover metric names before guessing a PromQL selector.\n\n"
    "doctor_metrics_labels(match: str?)\n"
    "  Discover label names for a metric/series.\n\n"
    "doctor_metrics_query(promql: str, start: str?, end: str?, step: str=15s)\n"
    "  PromQL: 'rate(http_requests_total[5m])', 'container_memory_usage_bytes',\n"
    "  'up'. Use start/end for range; omit for instant value.\n\n"
    "doctor_traces_recent(partition: str?, service: str?, from: str?, to: str?, limit: int=20,\n"
    "                       include_span_status: bool=true, errors_only: bool=false)\n"
    "  Trace summaries plus span status/error enrichment. Error traces are\n"
    "  sorted first and summarized in error_summary. Find slow or failing paths.\n\n"
    "doctor_logql_schema()\n"
    "  Real Loki stream labels + values (level, guardian_partition, ...).\n"
    "  Call this before writing a selector; level is a label, not body text.\n\n"
    "doctor_logql_label_values(label: str)\n"
    "  Values for one Loki label (e.g. level -> error/info/warn).\n\n"
    "doctor_logql_query(query: str, start: str?, end: str?, limit: int=100, direction: str=backward)\n"
    "  Native Loki LogQL against /loki/api/v1/query_range. Use for label\n"
    "  matchers, line filters, parsers, and metric queries.\n"
    "  Example: {guardian_partition=\"monofs\", level=\"warn\"}\n\n"
    "doctor_trace_detail(trace_id: str)\n"
    "  Every span in a trace: timings, status codes, attributes.\n\n"
    "doctor_traceql_query(query: str?, start: str?, end: str?, limit: int=20, spans_per_trace: int=0)\n"
    "  Native Tempo TraceQL search against /api/search.\n"
    "doctor_traceql_tags(start: str?, end: str?)\n"
    "  Discover TraceQL tag names.\n"
    "doctor_traceql_metrics(query: str, start: str?, end: str?, step: str?)\n"
    "  Tempo TraceQL metrics against /api/metrics/query_range.\n\n"
    "doctor_guardian_events(partition: str?, limit: int=20)\n"
    "  Deployment timeline: reconcile cycles, task completions, failures.\n\n"
    "doctor_event_analysis(partition: str?, tenant: str='default', limit: int=80)\n"
    "  Summarizes Guardian events: failures/successes, impacted partitions,\n"
    "  and prioritized failure signals for root-cause analysis.\n\n"
    "agent_sso_status()\n"
    "  OIDC/SSO state: configured, authenticated, source, subject, expiry.\n"
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

_STEVE_HELP_TEXT = (
    "You are Software Steve, the coding persona. Agent mode uses structured tools; terminal mode gives full bash.\n\n"
    "=== SLASH COMMANDS ===\n"
    "/help       — This reference.\n"
    "/mode [agent|terminal] — Switch between agent and terminal mode. Ctrl+E toggles.\n\n"
    "=== HOW TO USE TOOLS ===\n"
    "Output a single JSON line — nothing else:\n"
    '  {"tool": "workspace_status", "args": {}}\n'
    '  {"tool": "monofs_search", "args": {"query": "HandleRequest"}}\n'
    '  {"tool": "workspace_read", "args": {"path": "monofs/internal/fuse/commit.go"}}\n'
    "\n"
    "Mutation safety:\n"
    "- workspace_commit / workspace_push require explicit approval and `confirm=true` in args\n"
    "  (waived when autonomous push is enabled).\n"
    "- File edits via workspace_write are local overlay changes until committed.\n"
    "The result comes back, then you produce your final answer.\n\n"
    "=== CODING WORKFLOW ===\n"
    "  1. workspace_status()                       — Is the mount active? Any pending work?\n"
    "  2. monofs_search(query)                     — Locate the code across all repos.\n"
    "  3. workspace_list(path) / workspace_read()  — Inspect the surrounding code.\n"
    "  4. workspace_write(path, content)           — Make the change (full file content).\n"
    "  5. workspace_diff()                         — Review the change surface.\n"
    "  6. workspace_commit(message)                — Virtual commit (after approval).\n"
    "  7. workspace_push()                         — Push upstream (after approval).\n\n"
    "=== GUARDIAN AUTHORING ===\n"
    "  guardian_asset_describe(type)               — Schema/hints for an asset type.\n"
    "  guardian_partition_validate(root)           — Validate local bundles.\n"
    "  guardian_intent_plan(partition)             — Dry-run plan after edits.\n"
    "  Read `.github/skills/guardian-partition-authoring/SKILL.md` for the full playbook.\n\n"
    "=== TELEMETRY / SSO ===\n"
    "  doctor_* tools and guardian_* tools attach the agent OIDC/SSO token automatically.\n"
    "  agent_sso_status()                          — Check SSO state on a 401.\n"
    "  Read `.github/skills/query-agent-telemetry/SKILL.md` for queries and\n"
    "  `.github/skills/agent-sso-setup/SKILL.md` for SSO configuration.\n"
    "Use tools first, then answer with evidence and a clear next action."
)


def _help_text(persona_id: str) -> str:
    persona = get_persona(persona_id)
    if persona is not None and persona.id == "steve":
        return _STEVE_HELP_TEXT
    return _HELP_TEXT


# Tool instructions are auto-generated from the skill registry in tools.py and
# appended to the persona system prompt on every turn.


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


def _coerce_dsml_value(value: str):
    value = value.strip()
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _extract_dsml_tool_calls(text: str) -> list[dict]:
    """Convert DeepSeek DSML invoke markup into local tool-call shapes."""
    calls: list[dict] = []
    for match in _DSML_INVOKE_RE.finditer(text):
        tool_name = match.group(1).strip()
        if not tool_name:
            continue
        args = {
            param.group(1).strip(): _coerce_dsml_value(param.group(2))
            for param in _DSML_PARAM_RE.finditer(match.group(2))
        }
        calls.append({"tool": tool_name, "args": args})
    return calls


def _extract_tool_calls(text: str) -> list[dict]:
    """Return every tool call in a model response, preserving model order.

    The model sometimes emits several independent calls in one completion
    (for example partition detail + events + logs). Executing only the first
    one makes the model answer from partial evidence and invent the rest.
    """
    calls: list[dict] = []
    for match in _TOOL_RE.finditer(text):
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "tool" not in obj:
            continue
        if not isinstance(obj.get("args"), dict):
            obj["args"] = {k: v for k, v in obj.items() if k not in ("tool", "args")}
        calls.append(obj)
    if calls:
        return calls
    return _extract_dsml_tool_calls(text)


def _extract_tool_call(text: str) -> dict | None:
    """Backward-compatible single-call helper."""
    calls = _extract_tool_calls(text)
    return calls[0] if calls else None


def _strip_tool_calls(text: str) -> str:
    """Remove protocol markup so the model does not see its own call syntax."""
    stripped = _TOOL_RE.sub("", text)
    stripped = _DSML_INVOKE_RE.sub("", stripped)
    stripped = re.sub(
        rf"</?{_DSML_BAR}DSML{_DSML_BAR}[^>]*>", "", stripped, flags=re.IGNORECASE
    )
    return stripped.strip()


def _requires_tool_use(user_message: str) -> bool:
    return bool(_TELEMETRY_TERMS.search(user_message or ""))


def _metric_value_requested(user_message: str) -> bool:
    text = user_message or ""
    return bool(_METRIC_VALUE_TERMS.search(text) and _METRIC_VALUE_INTENT.search(text))


def _partition_hint(user_message: str) -> str:
    match = _PARTITION_HINT_RE.search(user_message or "")
    return match.group(1).lower() if match else ""


def _first_metric_name(results: list[tuple[str, str]]) -> str:
    for name, result in results:
        if name != "doctor_metrics_names":
            continue
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            continue
        names = payload.get("metric_names") if isinstance(payload, dict) else None
        if isinstance(names, list) and names:
            return str(names[0])
    return ""


def _tool_result_is_empty(result: str) -> bool:
    """Best-effort detection of an empty Doctor/Guardian result payload."""
    text = (result or "").strip()
    if not text:
        return True
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("returned") == 0:
        return True
    for key in ("results", "traces", "spans", "services", "partitions", "log_groups"):
        if isinstance(payload.get(key), list) and not payload[key]:
            return True
    return False


def _format_tool_results(results: list[tuple[str, str]]) -> str:
    blocks = ["[Tool results]"]
    empty_names: list[str] = []
    for name, result in results:
        blocks.append(f"## {name}\n{result}")
        if _tool_result_is_empty(result):
            empty_names.append(name)
    if empty_names:
        blocks.append(
            "Empty results: "
            + ", ".join(sorted(set(empty_names)))
            + ". Report these as empty; do not substitute invented data."
        )
    blocks.append("Now answer based only on these results. " + _EVIDENCE_REMINDER)
    return "\n\n".join(blocks)


async def _stream_text_once(text: str) -> AsyncIterator[str]:
    yield text


async def run_agent_turn(
    user_message: str,
    session_id: str,
    *,
    stream: bool = True,
    persona_id: str = DEFAULT_PERSONA_ID,
    profile: str = "local",
) -> AsyncIterator[str] | str:
    persona = get_persona(persona_id) or get_persona(DEFAULT_PERSONA_ID)
    if user_message.strip().lower() == "/help":
        help_text = _help_text(persona.id)
        await session_mgr.add_message(session_id, "user", user_message)
        await session_mgr.add_message(session_id, "assistant", help_text, persona=persona.id)
        if not stream:
            return help_text
        return _stream_text_once(help_text)

    history = await session_mgr.get_messages(session_id)

    system_prompt = persona_system_prompt(persona.id)
    allowed = allowed_tools(persona.id)
    # Always inject tool instructions, even if user customized the system prompt
    tool_instructions = build_tool_instructions(allowed)
    full_system_prompt = system_prompt + persona.operating_policy + tool_instructions
    if _metric_value_requested(user_message):
        full_system_prompt += (
            "\n\nThis turn asks for a metric value. Discover the metric name and "
            "labels if needed, then run doctor_metrics_query in this same turn. "
            "Do not stop at the catalog and do not present a catalog listing as "
            "the answer to a value question."
        )
    logger.info(
        "[LOOP] Starting %s turn (profile=%s). System prompt (first 200): %.200s",
        persona.id,
        profile,
        system_prompt,
    )
    messages = [{"role": "system", "content": full_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    await session_mgr.add_message(session_id, "user", user_message)

    require_tools = _requires_tool_use(user_message)
    metric_value = _metric_value_requested(user_message)
    partition_hint = _partition_hint(user_message)

    if not stream:
        result = await _run_tool_loop(
            messages,
            profile=profile,
            allowed=allowed,
            require_tools=require_tools,
            metric_value=metric_value,
            partition_hint=partition_hint,
        )
        await session_mgr.add_message(session_id, "assistant", result, persona=persona.id)
        return result

    return _stream_tool_loop(
        messages,
        session_id,
        profile=profile,
        allowed=allowed,
        persona_id=persona.id,
        require_tools=require_tools,
        metric_value=metric_value,
        partition_hint=partition_hint,
    )


async def _run_tool_loop(
    messages: list[dict],
    *,
    profile: str = "local",
    allowed: set[str] | None = None,
    require_tools: bool = False,
    metric_value: bool = False,
    partition_hint: str = "",
) -> str:
    """Non-streaming: run up to MAX_TOOL_ROUNDS of tool calls then return final answer."""
    nudged = False
    metric_nudged = False
    used_tools = False
    for round_num in range(MAX_TOOL_ROUNDS):
        response = await chat_completion(messages, stream=False, profile=profile)
        logger.info("[LOOP] LLM raw response (non-stream, round %d): %.500s", round_num, response)
        tool_calls = _extract_tool_calls(response)
        if not tool_calls:
            if require_tools and not nudged and not used_tools:
                logger.info("[LOOP] Telemetry request without a tool call; nudging model.")
                messages.append({"role": "system", "content": _TOOL_USE_NUDGE})
                nudged = True
                continue
            logger.info("[LOOP] No tool call detected, returning response.")
            return response

        used_tools = True
        assistant_text = _strip_tool_calls(response)
        results: list[tuple[str, str]] = []
        for tool_call in tool_calls:
            tool_name = str(tool_call.get("tool", ""))
            tool_args = tool_call.get("args", {})
            logger.info("Tool call (round %d): %s %s", round_num, tool_name, tool_args)
            tool_result = await execute_tool(tool_name, tool_args, allowed=allowed)
            logger.info("Tool result (%s): %.200s", tool_name, tool_result)
            results.append((tool_name, tool_result))

        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": _format_tool_results(results)})
        if (
            metric_value
            and not metric_nudged
            and {name for name, _ in results} <= {"doctor_metrics_names", "doctor_metrics_labels"}
        ):
            metric_name = _first_metric_name(results)
            if metric_name:
                selector = metric_name
                if partition_hint:
                    selector += f'{{guardian_partition="{partition_hint}"}}'
                promql = f"topk(5, {selector})"
                logger.info("Auto metric value query: %s", promql)
                auto_result = await execute_tool(
                    "doctor_metrics_query",
                    {"promql": promql, "compact": True},
                    allowed=allowed,
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
            else:
                messages.append({"role": "system", "content": _METRIC_QUERY_NUDGE})
            metric_nudged = True

    # Fallback: final completion after max rounds
    return await chat_completion(messages, stream=False, profile=profile)


def _text_chunks(text: str, size: int = 48) -> list[str]:
    """Split a final answer into display-sized chunks without breaking words."""
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            next_space = text.find(" ", end)
            if next_space != -1 and next_space - start <= size * 2:
                end = next_space + 1
        chunks.append(text[start:end])
        start = end
    return chunks


async def _stream_tool_loop(
    messages: list[dict],
    session_id: str,
    *,
    profile: str = "local",
    allowed: set[str] | None = None,
    persona_id: str = DEFAULT_PERSONA_ID,
    require_tools: bool = False,
    metric_value: bool = False,
    partition_hint: str = "",
) -> AsyncIterator[str]:
    """Detect tool calls, execute silently, then stream the final answer.

    Tool-detection rounds intentionally use non-streaming completions. DeepSeek
    returns its JSON tool-call line for non-streaming requests, but emits native
    DSML markup in streaming mode, which the loop cannot reliably parse.
    """
    working_messages = list(messages)
    nudged = False
    metric_nudged = False
    used_tools = False

    for round_num in range(MAX_TOOL_ROUNDS):
        response = await chat_completion(working_messages, stream=False, profile=profile)
        logger.info("[LOOP] LLM raw response (round %d): %.500s", round_num, response)

        tool_calls = _extract_tool_calls(response)
        if not tool_calls:
            if require_tools and not nudged and not used_tools:
                logger.info("[LOOP] Telemetry request without a tool call; nudging model.")
                working_messages.append({"role": "system", "content": _TOOL_USE_NUDGE})
                nudged = True
                continue
            logger.info("[LOOP] No tool call detected in round %d, streaming final answer.", round_num)
            for chunk in _text_chunks(response):
                yield chunk
            if response:
                await session_mgr.add_message(session_id, "assistant", response, persona=persona_id)
            return

        # Execute every call in this completion, not just the first one.
        used_tools = True
        results: list[tuple[str, str]] = []
        status_lines: list[str] = []
        for tool_call in tool_calls:
            tool_name = str(tool_call.get("tool", ""))
            tool_args = tool_call.get("args", {})
            logger.info("Tool call (round %d): %s %s", round_num, tool_name, tool_args)
            status_lines.append(_tool_status_text(tool_name, tool_args))

        # Yield one status block so the UI shows what is running.
        yield "\n\n" + "\n".join(f"*{line}*" for line in status_lines) + "\n\n"

        for tool_call in tool_calls:
            tool_name = str(tool_call.get("tool", ""))
            tool_args = tool_call.get("args", {})
            tool_result = await execute_tool(tool_name, tool_args, allowed=allowed)
            logger.info("Tool result (%s): %.200s", tool_name, tool_result)
            results.append((tool_name, tool_result))

        assistant_text = _strip_tool_calls(response)
        if assistant_text:
            working_messages.append({"role": "assistant", "content": assistant_text})
        working_messages.append({"role": "user", "content": _format_tool_results(results)})
        if (
            metric_value
            and not metric_nudged
            and {name for name, _ in results} <= {"doctor_metrics_names", "doctor_metrics_labels"}
        ):
            metric_name = _first_metric_name(results)
            if metric_name:
                selector = metric_name
                if partition_hint:
                    selector += f'{{guardian_partition="{partition_hint}"}}'
                promql = f"topk(5, {selector})"
                logger.info("Auto metric value query: %s", promql)
                auto_result = await execute_tool(
                    "doctor_metrics_query",
                    {"promql": promql, "compact": True},
                    allowed=allowed,
                )
                working_messages.append(
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
            else:
                working_messages.append({"role": "system", "content": _METRIC_QUERY_NUDGE})
            metric_nudged = True

    # Max rounds exhausted — do one final non-streaming call
    final = await chat_completion(working_messages, stream=False, profile=profile)
    yield final
    if final:
        await session_mgr.add_message(session_id, "assistant", final, persona=persona_id)


async def ensure_session(session_id: str | None) -> str:
    if session_id:
        existing = await session_mgr.get_session(session_id)
        if existing:
            return session_id

    result = await session_mgr.create_session()
    return result["id"]
