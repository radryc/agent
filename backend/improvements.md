Strata SRE Agent: CrewAI Flow & UI Design DocumentTarget File: backend/STRATA_CREWAI_ARCHITECTURE_DESIGN.md   Supersedes: backend/STRATA_DEVELOPER_HELPER_DESIGN.md   Scope: Backend Multi-Agent Orchestration (app/agent/*, app/nightwatch/*) & Frontend Mission Control / Chat / Nightwatch UI (frontend/src/*)   1. Problem Statement & Root-Cause DiagnosisUpon inspecting the current codebase, the repository has a strong operational foundation (NightwatchStore SHA-256 hash-chained SQLite ledger, MonoFS FUSE overlay workspace sandboxing, OIDC token propagation, and Loki/Tempo/PromQL payload compaction). However, the agent behaves unreliably—and the codebase currently suffers from a split-brain state between its tests and implementation—due to five specific issues:   Tool & Persona Overload on a 4B Quantized Model: OutageOtto runs on gemma-4-E4B-it-Q4_K_M.gguf inside an 8,192-token window (CTX_SIZE=8192, PARALLEL=1). Because allowed_tools("otto") returns None, all 35+ Guardian, Doctor, and MonoFS tool schemas are injected simultaneously alongside a heavy satirical persona prompt. Small quantized models suffer severe attention degradation when forced to select among 30+ tools while maintaining roleplay prose.   Brittle Regex Scraping & Band-Aid Heuristics in loop.py: The tool loop scrapes raw LLM text for {"tool": ...} or <DSML invoke> blocks and relies on fragile regex workarounds (_requires_tool_use, _metric_value_requested, nudging the model when it forgets to call a tool, and auto-querying the first metric returned by doctor_metrics_names).   Codebase Drift Between tests/ and backend/app/: Several unit tests (test_loop.py, test_hybrid.py, test_llm_config.py) test features (multi-tool extraction, grammar retries, hybrid profile escalation) against an older/newer revision of loop.py and client.py, while frontend/src/App.tsx only mounts /, /chat, and /settings (leaving DashboardPage.tsx, NightwatchPage.tsx, and EmbedPage.tsx unrouted or partially wired).   Disconnected Routing (router.py) & Split Brain (investigator.py): keyword_route() cannot handle mixed ops+code queries (e.g., "check the failing trace in monofs and patch the handler") because it routes to a single persona per turn. Meanwhile, Nightwatch (app/nightwatch/investigator.py) uses a hardcoded procedural query list (DEFAULT_METRIC_QUERIES) instead of sharing the interactive agent's diagnostic intelligence.   Single-Stream Terminal UI Without Agent Telemetry: ChatPage.tsx renders raw tokens into xterm.js without showing which sub-agent is active, which tool is executing, or providing interactive approval cards for confirm=true mutation gates.   2. Target Architecture: CrewAI Flows + Scoped Specialist CrewsWe replace the monolithic loop.py and procedural investigator.py with a single deterministic CrewAI Flow (StrataSREFlow) that orchestrates four tightly scoped specialist agents.                            +-----------------------------------+
                         |  Incoming Turn (/ws/chat,         |
                         |  /api/chat, /api/agent/proposal,  |
                         |  or NightwatchWorker Poll)        |
                         +-----------------+-----------------+
                                           |
                                           v
                         +-----------------------------------+
                         |   StrataSREFlow (@start/@router)  |
                         |   1. @mention / ViewContext check |
                         |   2. Intent & Partition resolver  |
                         +---+---------+---------+-------+---+
                             |         |         |       |
         +-------------------+         |         |       +-------------------+
         | (triage / RCA)              |         | (code_fix / proposal)     | (mutate_infra)
         v                             |         v                           v
+----------------------------------+   |   +---------------------------+  +--------------------------+
|   IncidentInvestigationCrew      |   |   |   CodeRemediationCrew     |  |   MutationGateHandler    |
|                                  |   |   |                           |  |                          |
|  +----------------------------+  |   |   |  +---------------------+  |  | - Requires confirm=true  |
|  | 1. TelemetryHound (Doctor) |  |   |   |  | SoftwareSteve       |  |  | - Executes reconcile /   |
|  |    5 tools | local LLM     |  |   |   |  | 10 tools | deepseek |  |  |   deploy / workspace_push|
|  +-------------+--------------+  |   |   |  +----------+----------+  |  +--------------------------+
|                | (parallel)      |   |   |             |             |
|  +-------------+--------------+  |   |   |  +----------v----------+  |
|  | 2. TopologyAuditor(Guardian|  |   |   |  | SpecValidator       |  |
|  |    5 tools | local LLM     |  |   |   |  | graph / partition   |  |
|  +-------------+--------------+  |   |   |  +---------------------+  |
|                |                 |   |   +-------------+-------------+
|                v                 |   |                 |
|  +----------------------------+  |   |                 |
|  | 3. OutageOtto (Commander)  |  |   |                 |
|  |    0 tools | synthesis LLM |  |   |                 |
|  +-------------+--------------+  |   |                 |
+----------------|-----------------+   |                 |
                 |                     |                 |
                 +----------+----------+-----------------+
                            | (if recommended_action == "patch_code", auto-route to Steve)
                            v
                 +-----------------------------------+
                 | Persist to Session / Nightwatch   |
                 | Stream Structured WS Events to UI |
                 +-----------------------------------+
2.1 Specialist Agent Decomposition & Tool ScopingTo keep prompt sizes under 2,500 tokens on gemma-4-E4B-it (or qwen3.5-9b), no agent is allowed more than 8 tools.   Agent IDRoleDefault LLM ProfileMax ToolsAssigned Tools (app/agent/tools.py)telemetry_houndDoctor Telemetry & Signal Correlation Specialistlocal (gemma-4-E4B-it / qwen3.5-9b + GBNF grammar)   8doctor_metrics_names, doctor_metrics_labels, doctor_metrics_query, doctor_logs_services, doctor_logs_search, doctor_logql_schema, doctor_logql_query, doctor_traces_recent, doctor_trace_detail   topology_auditorGuardian Topology & Deployment State Auditorlocal (gemma-4-E4B-it / qwen3.5-9b + GBNF grammar)   7guardian_overview, guardian_partitions, guardian_partition_detail, guardian_intent_activity, guardian_asset_describe, doctor_event_analysis, lb_edge_services   software_steveStaff Software & Observability Engineer (MonoFS)steve (opencode $\rightarrow$ deepseek-chat $\rightarrow$ local)   10monofs_search, monofs_status, workspace_status, workspace_list, workspace_read, workspace_write, workspace_diff, guardian_intent_plan, guardian_partition_validate, doctor_graph_validate   outage_ottoPrincipal SRE Incident Commander (Persona Synthesizer)fallback (gemini-2.5-flash / qwen3.5-plus) or local   0 (in RCA) / 3 (in Gated Exec)RCA Synthesis: 0 tools (pure reasoning over structured TelemetryReport + TopologyReport).Approved Execution: guardian_reconcile, guardian_deploy_partition, workspace_push.   Why OutageOtto has 0 tools during RCA: Separating tool execution (TelemetryHound & TopologyAuditor) from persona synthesis (OutageOtto) eliminates the conflict where the cynical PagerDuty roleplay prompt causes the 4B model to hallucinate snarky prose instead of emitting valid JSON tool calls.   3. Data Contracts & Pydantic ModelsCreate backend/app/agent/contracts.py to serve as the single source of truth across CrewAI tasks, NightwatchStore persistence, and WebSocket UI events.   Python# backend/app/agent/contracts.py
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class EvidenceRecord(BaseModel):
    kind: Literal["metric", "log", "trace", "topology", "code"]
    source: Literal["doctor", "guardian", "monofs"]
    query: str
    result_summary: str
    raw_hash: str = ""
    empty: bool = False


class ObservabilityGap(BaseModel):
    title: str
    category: Literal["metrics", "logs", "traces", "labels", "alerting", "dashboard", "docs"]
    rationale: str
    proposed_change: str
    target: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class TelemetryReport(BaseModel):
    partition: str
    metrics_queried: list[str] = Field(default_factory=list)
    anomalous_metrics: list[str] = Field(default_factory=list)
    error_logs: list[str] = Field(default_factory=list)
    failing_spans: list[str] = Field(default_factory=list)
    empty_signals: list[str] = Field(default_factory=list)
    observability_gaps: list[ObservabilityGap] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class TopologyReport(BaseModel):
    partition: str
    health_status: str = "unknown"
    drifted_intents: list[str] = Field(default_factory=list)
    failing_assets: list[str] = Field(default_factory=list)
    recent_events: list[str] = Field(default_factory=list)
    cross_partition_targets: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class RootCauseCandidate(BaseModel):
    partition: str
    service: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str


class IncidentVerdict(BaseModel):
    summary: str
    root_cause_partition: str
    root_cause_service: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    cross_partition: bool = False
    escalate: bool = False
    escalation_target: str = ""
    escalation_reason: str = ""
    recommended_action: Literal[
        "none", "notify", "ack", "silence", "escalate",
        "handoff", "reconcile", "restart", "scale", "rollback", "patch_code"
    ] = "none"
    recommendation_target: str = ""
    rationale: str = ""
    falsification: str = ""
    candidates: list[RootCauseCandidate] = Field(default_factory=list)
    improvements: list[ObservabilityGap] = Field(default_factory=list)
    otto_response: str = Field(
        default="",
        description="Final response written in OutageOtto's dry, technical SRE voice."
    )


class SteveProposalReport(BaseModel):
    files_changed: list[str] = Field(default_factory=list)
    unified_diff: str = ""
    validation_result: str = ""
    verification_command: str = ""
    summary: str = ""
4. Detailed Backend Module Specifications4.1 backend/app/agent/crew_tools.py (Pydantic-Validated Tool Wrappers)Instead of passing untyped dicts into execute_tool, wrap each skill in app/agent/tools.py as a crewai.tools.BaseTool with strict Pydantic args_schema validation:   Enforced Invariants at Schema Level:GuardianPartitionDetailSchema: Validates include in {"summary", "manifest", "full"} and enforces that asset or include="manifest" requires intent to be non-empty (preserving tests/test_guardian_detail.py).   DoctorLogQLQuerySchema: Automatically normalizes {level="warn"} $\rightarrow$ {level=~"(?i)warn"} via _normalize_logql_severity before hitting Doctor (preserving tests/test_loop.py).   MutationConfirmSchema: Any tool marked mutating=True (guardian_reconcile, guardian_deploy_partition, workspace_commit, workspace_push) checks confirm=True or auto_push_enabled before execution (preserving tests/test_workspace.py).   4.2 backend/app/agent/guardrails.py (Deterministic Task Guardrails)CrewAI supports guardrail: Callable[[TaskOutput], tuple[bool, Any]] on every Task. We replace the brittle heuristics in loop.py with three formal task guardrails:   metric_catalog_followup_guardrail:Rule: If the user asked for a metric value (_metric_value_requested(msg) == True) and TelemetryHound called doctor_metrics_names without calling doctor_metrics_query, reject the task output with instructions: "You queried the metric catalog (doctor_metrics_names) but did not query live values. Call doctor_metrics_query on the discovered metric name before finishing."   zero_fabrication_guardrail:Rule: Inspects tool outputs recorded during the task. If every tool returned "returned": 0 or "result": [] (_tool_result_is_empty), verifies that the agent's output explicitly reports empty data and does not fabricate numbers, pod names, or trace IDs.   nightwatch_policy_guardrail:Rule: Enforces Nightwatch safety rules on IncidentVerdict:If root_cause_partition != alerting_partition, forces cross_partition = True, escalate = True, recommended_action = "handoff", and escalation_target = root_cause_partition (preserving tests/test_nightwatch.py).   If confidence < 0.5, forces escalate = True and recommended_action = "escalate".   4.3 backend/app/agent/sre_flow.py (The Unified Orchestrator)StrataSREFlow(Flow[SREFlowState]) coordinates both interactive chat turns and background Nightwatch investigations:   @start() classify_turn():Checks for /help or View context: <Product> · <Path> (auto-runs strata_view_context starter tool first, preserving tests/test_view_context.py).   Uses router.resolve_persona() (manual | mention | keyword | sticky | classifier), enhanced with a new "crew_collab" route when a prompt contains both ops and code signals (e.g., "check logs and fix the bug in monofs").   @listen("triage") run_Diagnostic_crew():Runs TelemetryHound and TopologyAuditor in parallel (async_execution=True), followed by OutageOtto synthesizing IncidentVerdict.   Streams real-time step events (agent_step, tool_start, tool_end) to the WebSocket callback.   @router(run_diagnostic_crew) check_code_handoff():If the user requested a code/manifest/dashboard fix OR verdict.recommended_action == "patch_code", transitions directly to run_steve_crew() in the same turn.   @listen("code_fix") run_steve_crew():Runs SoftwareSteve to search MonoFS, write to /data/monofs-overlay, validate graphs/manifests (doctor_graph_validate / guardian_partition_validate), and return a SteveProposalReport with the unified diff.   @listen("mutate_infra") request_or_execute_mutation():If confirm=true is not present, emits an interactive approval_required WebSocket event so the operator can click Approve & Execute in the UI.   4.4 Unifying backend/app/nightwatch/investigator.pyRefactor Investigator.investigate() in backend/app/nightwatch/investigator.py so that:It retains deterministic pre-collection of DEFAULT_METRIC_QUERIES and _collect_fleet_anomalies() for fast baseline hashing in NightwatchStore.   Instead of dumping a static JSON blob into a single Assessor prompt, it invokes StrataSREFlow(mode="nightwatch_shadow") when deeper log/trace/topology correlation is needed, and records each specialist agent's findings as a cryptographically hash-chained event (agent.telemetry_hound, agent.topology_auditor, agent.outage_otto) in NightwatchStore.append_event().   Keeps 100% backward compatibility with FakeGuardian, FakeDoctor, and FakeAssessor in tests/test_nightwatch.py.   4.5 Infrastructure Tuning (docker-compose.yml & guardian/intents/10-lagent-stack.yaml)Parallel Slot Allocation in llama-server: Update PARALLEL=2 and CTX_SIZE=16384 in .env, docker-compose.yml, and 10-lagent-stack.yaml. Because llama-server runs with --cont-batching and --flash-attn auto, PARALLEL=2 allows TelemetryHound and TopologyAuditor to execute tool calls concurrently without serial queue blocking.   5. Frontend UI & UX Design SpecificationInspecting frontend/src/ revealed that DashboardPage.tsx, NightwatchPage.tsx, EmbedPage.tsx, TopBar.tsx, ContextRail.tsx, ChatMessages.tsx, and AuthGate.tsx already exist in the repository alongside a rich Strata design system (tokens.css and ui.css), but App.tsx and Sidebar.tsx do not route or expose most of them!   5.1 Application Shell & Routing (App.tsx, AppLayout.tsx, Sidebar.tsx, TopBar.tsx)Mount All Existing Pages in App.tsx:/ $\rightarrow$ LandingPage (with ShiftHandover card embedded when Nightwatch has activity)   /dashboard $\rightarrow$ DashboardPage (Guardian Mission Control & Crew Runtime Snapshot)   /chat & /chat/:sessionId $\rightarrow$ ChatPage (Dual Split-View: Rich Agent Workspace + XTerm Terminal)   /nightwatch $\rightarrow$ NightwatchPage (Shadow SRE Observer, Hash-Chained Timeline, & 1-Click Steve Diff Handoff)   /embed $\rightarrow$ EmbedPage (Standalone "Ask Strata" iframe drawer for Doctor/Guardian/MonoFS)   /settings $\rightarrow$ SettingsPage (Persona Prompts, Local Preset Selector, Hybrid Escalation, SSO/OIDC, & MonoFS Status)   Integrate TopBar, NightwatchBanner, and CommandPalette (Ctrl+K) into AppLayout.tsx:Mount TopBar.tsx and NightwatchBanner.tsx at the top of AppLayout.tsx so operators always see active Nightwatch escalations, SSO authentication status (SsoStatusButton.tsx), and theme switching (ThemePicker.tsx).   5.2 UpgradedWebSocket Protocol (frontend/src/types/index.ts & chatStore.ts)Extend WSIncoming to carry structured CrewAI telemetry alongside raw tokens:   TypeScriptexport interface CrewStepEvent {
  agent_id: "telemetry_hound" | "topology_auditor" | "software_steve" | "outage_otto";
  agent_name: string;
  task_name: string;
  status: "started" | "tool_call" | "tool_result" | "completed" | "failed";
  tool?: string;
  tool_args?: Record<string, unknown>;
  summary?: string;
  ts: string;
}

export interface ApprovalRequestEvent {
  id: string;
  tool: "guardian_reconcile" | "guardian_deploy_partition" | "workspace_commit" | "workspace_push";
  args: Record<string, unknown>;
  rationale: string;
  diff?: string;
}
When type === "persona" arrives ({persona: "otto" | "steve", engine: "...", reason: "..."}), update the active persona badge in ChatPage and ContextRail.   When type === "crew_step" arrives, append it to activeCrewSteps in chatStore.ts AND write a compact ANSI status line into Terminal.tsx (\x1b[36m[TelemetryHound]\x1b[0m doctor_metrics_query(promql="...")).   When type === "approval_required" arrives, render an interactive Mutation Approval Card (Approve & Run / Reject).   5.3 Dual-View ChatPage.tsx (Chat Workspace + XTerm Terminal + ContextRail)Currently, ChatPage.tsx only mounts Terminal.tsx, leaving ChatMessages.tsx, ChatInput.tsx, MessageBubble.tsx, CodeBlock.tsx, and ContextRail.tsx unused outside EmbedPage.tsx.   View Toggle in Status Bar (Chat View | Split View | Terminal View):Chat View: Uses ChatMessages.tsx + ChatInput.tsx with rich Markdown, syntax-highlighted diffs (CodeBlock.tsx), collapsible Crew Execution Traces (showing TelemetryHound $\rightarrow$ TopologyAuditor $\rightarrow$ OutageOtto steps), and One-Click Mutation Approval Cards.   Terminal View: Full-screen xterm.js (Terminal.tsx) with /mode [agent|terminal] and Ctrl+E PTY shell toggling.   Split View: Side-by-side rich Agent Chat on the left and live xterm.js PTY shell on the right, flanked by ContextRail.tsx showing the active Persona (Auto | Otto | Steve), active Crew step pipeline, MonoFS mount status, and recent shell commands.   5.4 DashboardPage.tsx Cleanup & Crew Telemetry PanelReplace Placeholder Card: Remove the placeholder "Reference Image — Prompt text hidden. Waiting for your image." card in DashboardPage.tsx.   Add "CrewAI Pipeline & Nightwatch Status" Card: Display real-time counts of active watched partitions, open Nightwatch incidents, active LLM profiles (Local Gemma-4 / Qwen3.5 vs. Steve DeepSeek/OpenCode), and MonoFS overlay status (mounted / writable).   5.5 SettingsPage.tsx Complete Strata ConfigurationUpdate SettingsPage.tsx to expose all backend runtime settings supported by app/config.py and app/agent/personas.py:   Persona Selector & Prompts: Separate tabs for OutageOtto (system_prompt), Software Steve (steve_system_prompt, steve_engine, steve_llm_base_url, steve_llm_model, steve_llm_api_key), and Auto-Routing / Hybrid Escalation (auto_switch_enabled, llm_hybrid_enabled, llm_hybrid_after_rounds, llm_grammar_enabled).   Local LLM Presets (presets.py): One-click selector between gemma-4-E4B-it, qwen3.5-9b (recommended for multi-agent tool calling), and qwen3.5-4b.   MonoFS & Mutation Safety: Live status indicator for /workspace FUSE mount and toggle for auto_push_enabled.   SSO / OIDC Device Flow: Interactive button to trigger /api/auth/device/start and /api/auth/login when Doctor/Guardian require per-user OIDC tokens.   6. Step-by-Step Implementation Plan & Verification MatrixPhase 1: Reconcile Core Backend & Unittest SuiteUpdate backend/requirements.txt to add crewai>=0.108.0 (or lightweight local Flow/Crew runner compatible with air-gapped Docker builds).   Ensure backend/app/config.py, backend/app/llm/client.py, and backend/app/agent/loop.py expose all functions and settings expected by the existing test suite (runtime_bool, llm_hybrid_enabled, llm_grammar_enabled, _extract_tool_calls, _requires_tool_use, _metric_value_requested, _tool_result_is_empty, _format_tool_results).   Mount auth_router, nightwatch_proxy_router, and proposal_router in backend/app/main.py (currently missing from main.py router includes!).   Phase 2: Implement CrewAI Multi-Agent EngineCreate backend/app/agent/contracts.py (Pydantic outputs for Telemetry, Topology, Verdict, and Steve Proposals).Create backend/app/agent/crew_tools.py and backend/app/agent/guardrails.py.Create backend/app/agent/sre_flow.py implementing StrataSREFlow and wire it into loop.py (run_agent_turn), proposal.py (_run_proposal), and nightwatch/investigator.py (Investigator.investigate).   Phase 3: Frontend Integration & UI PolishUpdate frontend/src/App.tsx, AppLayout.tsx, and Sidebar.tsx to route /dashboard, /nightwatch, /chat, /embed, and /settings, and mount TopBar + NightwatchBanner.   Upgrade frontend/src/pages/ChatPage.tsx and ContextRail.tsx to support Chat / Split / Terminal views, live Crew Step Visualization, Persona Switcher (Auto | Otto | Steve), and Mutation Approval Cards.   Replace the placeholder card in DashboardPage.tsx with the CrewAI & Nightwatch Telemetry Snapshot.   Expand SettingsPage.tsx with Software Steve, Hybrid LLM, OIDC SSO, and MonoFS Workspace controls.   Phase 4: Verification MatrixRun the full test suite and build verification before packaging:PYTHONPATH=backend pytest tests/ (or python3 -m unittest discover -s tests) — verify all 11 test modules pass (test_auth_api, test_graph_skill, test_guardian_detail, test_hybrid, test_llm_config, test_loop, test_nightwatch, test_oidc, test_personas, test_proposal, test_router, test_view_context, test_workspace, plus new test_sre_flow.py).   cd frontend && npm run build — verify zero TypeScript (tsc -b) or Vite bundling errors.   Shall I proceed with executing Phases 1–4 against your uploaded agent.zip and give you the updated archive with all tests passing and the frontend built?   
