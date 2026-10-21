# Strata Developer Helper Design

## Goal

Turn the local agent into a comprehensive Strata engineering assistant that can:

1. Analyze Doctor events with actionable summaries.
2. Support MonoFS-driven software development workflows.
3. Plan and execute Guardian deployments safely.
4. Provide a secure remote VS Code setup plan.
5. Enforce proposal-first execution for state-mutating operations.

## Implemented Architecture

### 1. Tooling model

The backend skill registry (`app/agent/tools.py`) now includes:

- **Strata readiness**
  - `strata_stack_check` — verifies Doctor, Guardian, `monofs-session`, and `guardianctl` availability.
- **Doctor analysis**
  - `doctor_event_analysis` — summarizes Guardian events from Doctor (failure/success/unknown counts, impacted partitions, recent failure signals).
- **Guardian deployment**
  - `guardian_deploy_partition` — deploy workflow via `guardianctl` (`push` -> optional `reconcile` -> optional `wait`), with plan-only default.
- **Remote VS Code**
  - `remote_vscode_plan` — generates secure setup plans for `ssh`, `code_tunnel`, and `code-server`.
  - `remote_vscode_install` — optional installation executor for `code-server` or SSH server, plan-only by default.

### 2. Mutation safety and proposal-first gate

- `Skill` now supports `mutating: bool`.
- Mutating tools require `args.confirm=true` (or equivalent confirmation token).
- Tool instructions mark mutating tools with **[MUTATES STATE]**.
- `guardian_reconcile` and `guardian_deploy_partition` are gated as mutating tools.

This enforces the workflow:

1. Diagnose and collect evidence.
2. Propose a concrete plan.
3. Execute only after explicit approval.

### 3. Prompt and UX behavior

- Default runtime persona changed from a cynical SRE style to **StrataDev** (professional, comprehensive, proposal-first).
- `/help` documentation updated to include:
  - proposal-first and confirmation requirements,
  - new Strata tools,
  - Doctor event analysis,
  - deploy workflow semantics (plan-only by default).
- Agent loop appends a proposal-first operating policy to the active system prompt on every turn.

## Operational Notes

- `guardianctl` discovery supports:
  - `GUARDIANCTL_BIN` override,
  - `guardianctl` in `PATH`,
  - `/app/guardianctl` fallback.
- `guardian_deploy_partition` is intentionally conservative:
  - defaults to `plan_only=true`,
  - requires explicit confirm to mutate,
  - supports `include_secrets`, `remove_missing`, timeout, and desired status controls.

## Expected Outcomes

- Better first-response diagnostics from Doctor event streams.
- Cleaner transition from diagnosis -> proposal -> deployment.
- Safer mutation controls to prevent accidental reconcile/deploy actions.
- Practical remote IDE onboarding path without requiring immediate shell access.

---

# Persona System (Otto ↔ Steve)

## Goal

Two personas share one chat product and switch by context:

- **Outage Otto** — the SRE persona. Incidents, Doctor telemetry, Guardian
  deployments, system health. Runs on the local Gemma model.
- **Software Steve** — the software engineering persona. Locates, reads,
  edits, commits and pushes code in the writable MonoFS workspace. Pointable
  at an external coding engine (OpenCode server or any OpenAI-compatible API
  such as DeepSeek), with local Gemma as the last-resort fallback.

## Implemented Architecture

### 1. Persona registry (`app/agent/personas.py`)

- `Persona` dataclass: id, name, role, summary, avatar, accent, default
  prompt, operating policy, tool allowlist.
- Otto keeps full tool access (existing behavior); Steve gets a curated
  allowlist: general + doctor/guardian read-only views + monofs read +
  `workspace_*` coding tools. Mutating infra tools
  (`guardian_reconcile`, `guardian_deploy_partition`, `remote_vscode_install`)
  are excluded and enforced at `execute_tool(..., allowed=...)` time as well
  as in the generated tool instructions.
- Engine resolution for Steve: `STEVE_ENGINE` forces
  `opencode | deepseek | local`; unset/auto picks the first available in the
  order **OpenCode → external API → local Gemma**.

### 2. Context routing (`app/agent/router.py`)

Auto mode resolves the persona per turn:

1. Explicit `@otto` / `@steve` mention (mention is stripped from the prompt).
2. Strong keyword signal (ops vs. code vocabulary).
3. Sticky conversation: short/affirmative follow-ups stay with the current
   session persona.
4. Tiny LLM classifier (local model, low-token, short timeout) for ambiguous
   topic shifts; keyword fallback and default (Otto) when it fails.

Sessions remember their `active_persona`; assistant messages persist the
persona that wrote them. The UI can also pin a persona manually
(Auto | Otto | Steve).

### 3. Auth and engine routing (`app/llm/client.py`, `app/api/chat.py`)

- `chat_completion(..., profile=...)` supports two credential profiles: the
  local llama.cpp server (`LLM_*`) and the external coding API
  (`STEVE_LLM_BASE_URL` / `STEVE_LLM_MODEL` / `STEVE_LLM_API_KEY`).
- WS turns emit a `{"type":"persona"}` event (id, name, engine, reason)
  before tokens; Steve dispatches to the OpenCode server (cwd = the MonoFS
  mount so its own edit tools hit the workspace) or the tool loop with the
  external/local profile. Legacy `agent: local|opencode` payloads still map
  to Otto/Steve.

### 4. MonoFS writable workspace (`app/agent/workspace.py`, container)

- `backend/entrypoint.sh` starts `monofs-client --router ... --mount ...
  --overlay ... --writable` when `MONOFS_ROUTER` is set; docker-compose adds
  `/dev/fuse`, `SYS_ADMIN`, and `apparmor:unconfined` for the backend.
- Workspace tools (path-sandboxed to the mount root): `workspace_status`,
  `workspace_list`, `workspace_read`, `workspace_write`, `workspace_diff`,
  `workspace_session` (start/add/rm/discard/log/refs/branch), plus
  `workspace_commit` and `workspace_push` wrapped around `monofs-session`.
- `workspace_commit` / `workspace_push` are mutation-gated
  (confirm=true) unless the **autonomous push** toggle
  (`AUTO_PUSH_ENABLED` / Settings) is on. `discard` always requires confirm.

### 5. Settings and UI

- Runtime settings: `steve_engine`, `steve_llm_*`, `steve_system_prompt`,
  `auto_switch_enabled`, `auto_push_enabled`, `monofs_router/mount/overlay`;
  `system_prompt` remains Otto's prompt. Keys are masked in the API.
- Frontend: Auto | Otto | Steve switch in the sidebar, persona-aware avatar
  (Steve ships as a monogram until real assets land in `frontend/public`),
  per-message attribution badges, streaming "who's on it" label, engine badge,
  and a Steve settings card (engine, API profile, prompts, switches, MonoFS
  workspace status).

### 6. Operational notes

- New env vars: `STEVE_ENGINE`, `STEVE_LLM_BASE_URL`, `STEVE_LLM_MODEL`,
  `STEVE_LLM_API_KEY`, `AUTO_SWITCH_ENABLED`, `AUTO_PUSH_ENABLED`,
  `MONOFS_ROUTER`, `MONOFS_MOUNT`, `MONOFS_OVERLAY` (+`MONOFS_OVERLAY_DIR`
  for the session CLI).
- SQLite migration adds `sessions.active_persona` and `messages.persona` on
  startup; existing databases keep working.
- The Guardian docker pusher deploying this stack must grant the backend
  `/dev/fuse` + `SYS_ADMIN` for the writable mount to work; the partition
  intent carries the new env vars (`guardian/intents/10-lagent-stack.yaml`).

---

# SSO / OIDC for telemetry and infrastructure APIs

## Goal

Doctor gates logs and traces behind OIDC when access enforcement is enabled.
Guardian can also enforce per-partition access. The agent must present an
identity instead of calling those APIs anonymously.

## Implemented Architecture

- `app/auth/oidc.py` provides a process-wide `OIDCTokenProvider`:
  - static bearer token (`AGENT_OIDC_TOKEN`) for break-glass/pre-issued tokens,
  - OIDC client credentials (`AGENT_OIDC_CLIENT_ID` +
    `AGENT_OIDC_CLIENT_SECRET`),
  - interactive device-code flow (Settings -> SSO -> Sign in with SSO).
- Tokens are cached until shortly before `expires_in`; client-credentials
  requests are serialized with an async lock.
- `_doctor_get`, `_guardian_get`, `_guardian_post`, the Guardian proxy, and the
  MonoFS session/search tools attach the bearer token automatically.
- `GET /api/auth/status` reports `configured`, `authenticated`, `source`,
  `subject`, `expires_at`, and the last error.
- `POST /api/auth/device/start` + `POST /api/auth/device/poll` implement the
  device-code login. The Settings page opens the verification URL, shows the
  user code, and polls until approval.
- Secrets are masked by `/api/settings` and never returned to the browser.

## Configuration

```text
AGENT_OIDC_ISSUER
AGENT_OIDC_TOKEN_URL
AGENT_OIDC_DEVICE_AUTHORIZATION_URL
AGENT_OIDC_CLIENT_ID
AGENT_OIDC_CLIENT_SECRET
AGENT_OIDC_AUDIENCE
AGENT_OIDC_SCOPE
AGENT_OIDC_TOKEN
```

For in-cluster Authentik, the token URL must be the in-cluster issuer URL
(`http://authentik.authentik.svc.cluster.local:9000/application/o/token/`),
otherwise Doctor rejects the token because the issuer does not match.

## Operational notes

- Device-flow state is process-global; the current agent is designed as a
  single-operator assistant. Multi-user deployments should key device flows by
  session.
- A 401 from Doctor after successful login usually means issuer/audience
  mismatch, an expired token, or a provider that requires client
  authentication on the device token request.
- Do not disable Doctor access enforcement to work around an auth failure.
