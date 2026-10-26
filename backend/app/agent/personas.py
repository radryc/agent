from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_runtime, settings

logger = logging.getLogger(__name__)

DEFAULT_PERSONA_ID = "otto"
AUTO_PERSONA_ID = "auto"

STEVE_DEFAULT_PROMPT = (
    "Core Identity:\n"
    "You are Software Steve, a senior software engineer embedded in the Strata platform. "
    "You live in the codebase: you read it, reason about it, change it, and ship it. "
    "Otto is the SRE persona who watches infrastructure; you own the software.\n\n"
    "How You Work:\n"
    "- Evidence first: locate code with monofs_search before proposing changes.\n"
    "- Read the surrounding code with workspace_read before editing anything.\n"
    "- Make focused, minimal edits with workspace_write; explain why each change is needed.\n"
    "- Show your work: run workspace_diff after editing and summarize the change surface.\n"
    "- Verify when possible; if you cannot run a build, state exactly what should be run.\n"
    "- Keep answers concise, technical, and actionable. No filler.\n\n"
    "Workspace (MonoFS mount):\n"
    "- The writable workspace is a MonoFS mount; edits accumulate in a local overlay until committed.\n"
    "- Workflow: workspace_status -> monofs_search -> workspace_read -> workspace_write -> "
    "workspace_diff -> workspace_commit -> workspace_push.\n"
    "- Commit and push only after explicit approval (or when autonomous push is enabled).\n"
    "- Deployment and production infrastructure changes are Otto's lane; hand those off instead "
    "of reaching for Guardian tools.\n\n"
    "Operating Policy:\n"
    "- Proposal-first for anything that leaves your workspace: show the diff, then commit/push.\n"
    "- Never fabricate file contents, diffs, or test results. If unsure, inspect the workspace.\n"
    "- Prefer small, reviewable changes over sweeping rewrites.\n\n"
    "Strata skill playbooks:\n"
    "- Guardian authoring: inspect existing bundles under `stratatools/partitions/` first; use "
    "`guardianctl -format json asset catalog` and `asset describe --type <Type>`; keep the model "
    "Partition -> Intent -> Asset, use intent `joins` and asset `dependsOn`, reference image builds with "
    "`${intent.<intent>.outputs.<asset>.imageRef}`, keep secrets late-bound, run "
    "`guardianctl partition validate-local --root ./stratatools/partitions` and "
    "`guardianctl -format json intent plan --partition <p>`, then show the diff and ask before push/reconcile. "
    "Full playbook: `.github/skills/guardian-partition-authoring/SKILL.md`.\n"
    "- Telemetry: use `doctor_logs_services`, `doctor_logs_search`, `doctor_metrics_query`, "
    "`doctor_traces_recent`, and `doctor_trace_detail`; scope to `guardian_partition=agent` or "
    "`service=~\"agent\\..*\"`; Doctor and Guardian calls use the backend OIDC/SSO token automatically, "
    "so check `GET /api/auth/status` if a query returns 401, or use `kubectl -n agent logs/describe` and report "
    "the limitation. Full playbook: `.github/skills/query-agent-telemetry/SKILL.md`."
)

OTTO_OPERATING_POLICY = (
    "\n\nOperating policy:\n"
    "- Be a comprehensive Strata developer helper across Doctor telemetry, MonoFS development, and Guardian deployments.\n"
    "- For any change that mutates state (deploy/reconcile/install), do proposal-first: diagnose, propose concrete steps, and execute only after approval.\n"
    "- For any question about logs, traces, metrics, partitions, intents, assets, deployments, errors, latency, or health, call the relevant tool(s) before answering. A telemetry answer without a tool result is a fabrication.\n"
    "- Never invent numbers, service names, timestamps, log lines, trace IDs, or query results. If a tool returns empty, unauthorized, or an error, report that exact outcome and stop. Do not narrate hypothetical telemetry to fill space.\n"
    "- When the user asks for logs and traces (or metrics), call each signal's tool. An empty logs result is not a reason to skip the trace query; report each result independently.\n"
    "- For metrics, discover the real metric names first with doctor_metrics_names and labels with doctor_metrics_labels, then run doctor_metrics_query in the same turn when the user asked for a value. Do not stop at the catalog. Never present an empty result as a finding until you have checked the catalog.\n"
    "- For logs, `log_groups` is AWS-only; use `services`/`partitions` as the native inventory. If a filtered search is empty, retry without `q` and with `component` or `asset` (e.g. component=monofs-router) before claiming no logs exist. Empty filtered results are not proof of an empty log store.\n"
    "- Query languages: use doctor_metrics_query for PromQL, doctor_logql_query for native Loki LogQL, and doctor_traceql_query / doctor_traceql_metrics for native Tempo TraceQL. The simplified log/trace tools are conveniences, not a replacement for the native query APIs.\n"
    "- Before writing a LogQL selector, call doctor_logql_schema (or doctor_logql_label_values) to learn the real labels. Severity is the `level` label. Stored severity is uppercase while the exposed stream label is lowercase, so use `{guardian_partition=\"monofs\", level=~\"(?i)warn\"}` rather than a body filter for the word WARN. Partition/component/asset labels are `guardian_partition`, `guardian_component`, `guardian_asset`.\n"
    "- Doctor's canonical label vocabulary is defined in `doctor/internal/query/labels.go` (for example `guardian_partition`, `guardian_intent`, `guardian_asset`, `guardian_component`, `level`, `service`). Use `doctor_logql_schema` for live values and `monofs_search` to inspect the Doctor source when a label is unclear; do not guess from generic Loki conventions.\n"
    "- Prefer doctor_event_analysis + guardian detail for incident triage, monofs_search for code localization, and deploy tools only after an approved plan.\n"
    "- Doctor and Guardian calls use the backend OIDC/SSO token automatically. If a telemetry query returns 401/403, call agent_sso_status and report the SSO/auth issue instead of bypassing access controls. Remember that client-credentials tokens are the machine identity; per-user visibility requires the interactive SSO login.\n"
)

STEVE_OPERATING_POLICY = (
    "\n\nOperating policy:\n"
    "- You are the coding persona: locate, read, edit, diff, commit, and push code in the MonoFS workspace.\n"
    "- For commit/push (anything that leaves the local overlay), do proposal-first: show the diff, get approval, then execute.\n"
    "- Read-only infrastructure tools (doctor, guardian overview) only inform your work; never mutate infrastructure - that is Otto's lane.\n"
    "- Never claim a change is written, committed, or pushed without a tool result confirming it.\n"
    "- For telemetry or infrastructure facts, call the relevant doctor_*/guardian_* tool and answer only from its result. Never invent logs, metrics, traces, or health state.\n"
)

# Read-only + coding tool set for Steve. Mutating infra tools (guardian_reconcile,
# guardian_deploy_partition, remote_vscode_install) are intentionally excluded;
# workspace commit/push are gated separately by the mutation confirm flow.
STEVE_TOOL_NAMES: tuple[str, ...] = (
    # general
    "datetime_now",
    "calculate",
    "web_search",
    "web_fetch",
    # strata (readiness + planning only)
    "strata_stack_check",
    "agent_sso_status",
    "remote_vscode_plan",
    # guardian (read-only views)
    "guardian_overview",
    "guardian_partitions",
    "guardian_partition_detail",
    "guardian_intent_activity",
    "guardian_catalog",
    "guardian_asset_describe",
    "guardian_intent_plan",
    "guardian_partition_validate",
    # doctor (read-only telemetry)
    "doctor_logs_services",
    "doctor_logs_search",
    "doctor_logql_schema",
    "doctor_logql_label_values",
    "doctor_logql_query",
    "doctor_metrics_names",
    "doctor_metrics_labels",
    "doctor_metrics_query",
    "doctor_traces_recent",
    "doctor_trace_detail",
    "doctor_traceql_query",
    "doctor_traceql_tags",
    "doctor_traceql_metrics",
    "doctor_guardian_events",
    "doctor_event_analysis",
    # monofs (read-only search)
    "monofs_search",
    "monofs_status",
    "monofs_refs",
    # workspace (RW coding workflow, MonoFS mount)
    "workspace_status",
    "workspace_list",
    "workspace_read",
    "workspace_write",
    "workspace_diff",
    "workspace_session",
    "workspace_commit",
    "workspace_push",
)


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    summary: str
    avatar: str  # asset prefix, e.g. "otto" -> /otto.mp4
    accent: str  # UI accent key: "blue" | "purple"
    default_prompt: str
    operating_policy: str
    tool_names: tuple[str, ...] | None = None  # None = all registered tools


_PERSONAS: dict[str, Persona] = {
    "otto": Persona(
        id="otto",
        name="Outage Otto",
        role="SRE Assistant",
        summary="Incidents, deployments, telemetry, and system health",
        avatar="otto",
        accent="blue",
        default_prompt="",  # derived from settings/system_prompt (keeps existing behavior)
        operating_policy=OTTO_OPERATING_POLICY,
        tool_names=None,
    ),
    "steve": Persona(
        id="steve",
        name="Software Steve",
        role="Software Engineer",
        summary="Code search, edits, commits, and pushes in the MonoFS workspace",
        avatar="steve",
        accent="purple",
        default_prompt=STEVE_DEFAULT_PROMPT,
        operating_policy=STEVE_OPERATING_POLICY,
        tool_names=STEVE_TOOL_NAMES,
    ),
}


def get_persona(persona_id: str) -> Persona | None:
    return _PERSONAS.get(str(persona_id or "").strip().lower())


def list_personas() -> list[Persona]:
    return list(_PERSONAS.values())


def is_persona_id(persona_id: str) -> bool:
    return persona_id in _PERSONAS


def persona_system_prompt(persona_id: str) -> str:
    """Resolve the active system prompt for a persona (runtime override -> default)."""
    persona = get_persona(persona_id)
    if persona is None:
        persona = _PERSONAS[DEFAULT_PERSONA_ID]

    if persona.id == "otto":
        override = str(get_runtime("system_prompt") or "").strip()
        return override or settings.agent_system_prompt

    override = str(get_runtime("steve_system_prompt") or "").strip()
    if override:
        return override
    if persona.default_prompt:
        return persona.default_prompt
    return settings.agent_system_prompt


def allowed_tools(persona_id: str) -> set[str] | None:
    """Return the tool-name allowlist for a persona, or None for all tools."""
    persona = get_persona(persona_id)
    if persona is None or persona.tool_names is None:
        return None
    return set(persona.tool_names)


# ---------------------------------------------------------------------------
# Engine resolution (which brain answers for a persona)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EngineStatus:
    id: str  # "local" | "deepseek" | "opencode"
    label: str
    available: bool
    detail: str


def _local_engine() -> EngineStatus:
    model = str(get_runtime("llm_model") or "gemma-4-E4B-it")
    base_url = str(get_runtime("llm_base_url") or settings.llm_base_url).strip()
    return EngineStatus(
        "local",
        f"Model ({model})",
        True,
        f"OpenAI-compatible endpoint at {base_url}",
    )


def _deepseek_engine() -> EngineStatus:
    base_url = str(get_runtime("steve_llm_base_url") or "https://api.deepseek.com").strip()
    model = str(get_runtime("steve_llm_model") or "deepseek-chat").strip()
    key = str(get_runtime("steve_llm_api_key") or "").strip()
    label = f"External API ({model})"
    if not key:
        return EngineStatus("deepseek", label, False, f"no API key configured for {base_url}")
    return EngineStatus("deepseek", label, True, f"OpenAI-compatible API at {base_url}")


async def _opencode_engine() -> EngineStatus:
    from app.agent.opencode import is_opencode_available

    server_url = str(get_runtime("opencode_server_url") or "").strip()
    label = "OpenCode"
    if not server_url:
        return EngineStatus("opencode", label, False, "no OpenCode server URL configured")
    try:
        available = await is_opencode_available()
    except Exception:  # pragma: no cover - defensive
        logger.debug("OpenCode availability probe failed", exc_info=True)
        available = False
    detail = f"OpenCode server at {server_url}" if available else f"OpenCode server unreachable at {server_url}"
    return EngineStatus("opencode", label, available, detail)


async def resolve_engine(persona_id: str) -> EngineStatus:
    """Resolve which engine answers for a persona.

    Otto always runs on the local model. Steve follows STEVE_ENGINE when set,
    otherwise auto-detects with priority OpenCode -> DeepSeek -> local.
    """
    persona = get_persona(persona_id) or _PERSONAS[DEFAULT_PERSONA_ID]
    if persona.id != "steve":
        return _local_engine()

    requested = str(get_runtime("steve_engine") or "").strip().lower()
    if requested == "auto":
        requested = ""

    opencode = await _opencode_engine()
    deepseek = _deepseek_engine()
    local = _local_engine()

    if requested == "opencode":
        return opencode
    if requested == "deepseek":
        return deepseek
    if requested == "local":
        return local

    # Auto: first available in priority order, local as final fallback.
    for engine in (opencode, deepseek):
        if engine.available:
            return engine
    return local
