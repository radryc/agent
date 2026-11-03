---
name: ask-strata-view
description: "Use when the user opens Ask Strata from Doctor, Guardian, MonoFS, or LB Edge, or asks what the current UI view shows, what to check first, or why a view is empty/failing."
---

# Ask Strata about this view

The Ask drawer passes a context string such as:

```text
Doctor · /logs
Guardian · /?partition=monofs&panel=topologyPanel
MonoFS · /cluster
LB Edge · /
```

Use `strata_view_context(product, path)` first. It resolves the product/route
and runs a live starter query using the native tools.

## Protocol

1. Call `strata_view_context` with the product and path from the context.
2. Read the starter result. If it is empty, unauthorized, or failed, say that
   explicitly.
3. Follow up with the tools listed in the tool result when the starter result
   is not enough. Do not guess.
4. Answer in three short parts:
   - what this view shows,
   - what to check first and why,
   - the evidence you actually queried.
5. Keep it concise. This is an orientation prompt, not a full incident report.

## Product → tools

### Doctor

| View | Primary tools |
|---|---|
| On-call `/` | `doctor_event_analysis` |
| Dashboards `/dashboards` | `doctor_metrics_names`, then `doctor_metrics_query` |
| APM Services `/services` | `doctor_logs_services`, `doctor_traces_recent` |
| Federation `/federation` | `guardian_overview` |
| Alerts `/alerts` | `doctor_guardian_events`, `guardian_partition_detail` |
| Recording Rules `/rules` | `doctor_metrics_names`, `doctor_metrics_query` |
| Traces `/traces` | `doctor_traces_recent`, `doctor_trace_detail` |
| Logs `/logs` | `doctor_logs_services`, `doctor_logs_search`, `doctor_logql_query` |
| Metrics `/metrics` | `doctor_metrics_names`, `doctor_metrics_labels`, `doctor_metrics_query` |
| Fleet `/fleet` | `guardian_overview`, `guardian_partitions` |
| Synthetics `/probes` | `guardian_overview`, `doctor_metrics_query` with `guardian_probe_up` |
| Partition `/partition/<name>` | `guardian_partition_detail`, then partition-scoped Doctor tools |

### Guardian

- `guardian_overview` for the partition dashboard.
- `guardian_partition_detail(partition=...)` when the URL has `?partition=...`.
- `guardian_intent_activity` when the view names an intent.
- `doctor_guardian_events` for deployment/reconcile history.

### MonoFS

- `monofs_status` for workspace/daemon state.
- `monofs_refs` for repositories and commit refs.
- `monofs_search` for code localization.

### LB Edge

- `lb_edge_services` lists account, external port, protocol, description, and
  backends registered by `lb-k8s-agent`.
- If a route is missing, check the source Service annotations
  (`guardian.intent/expose`, `guardian.intent/service-name`,
  `guardian.intent/external-port`) and `lb-k8s-agent` logs.

## Evidence rules

- Never invent telemetry, routes, ports, or service names.
- An empty result is evidence: say "no series/logs/traces returned" and list
  what you checked.
- Report the time window for log/trace/metric queries.
- Do not mutate infrastructure from an Ask view. Diagnose first; propose changes
  only when the user asks for them.
