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
