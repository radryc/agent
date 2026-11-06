---
name: create-doctor-graphs
description: "Use when the user asks to create, generate, modify, or scaffold Doctor graphs, charts, panels, saved graphs, or dashboards from a natural-language request, including JSON/YAML files that can be imported into Doctor."
---

# Create Doctor graphs from a request

Use this skill when the user says things like:

- "Create a graph for API latency"
- "Make me a CPU graph for the monofs partition"
- "Add a gauge for error rate"
- "Save a dashboard with request rate, p95, and error logs"
- "Generate a JSON/YAML graph file I can import"

## Goal

Turn the request into a **valid, evidence-backed Doctor graph definition** that
the user can import or add to a dashboard. Do not guess metric names, labels, or
query shapes.

## 1. Choose the output format

| Request | Output |
|---|---|
| One or a few line charts for personal/local use | **Saved Graph Bundle v1** |
| A shareable multi-panel dashboard | **Doctor Dashboard v1** |
| A shared graph that must honor partition RBAC | **Doctor Dashboard v1**, even for one panel |
| Several dashboards | **Doctor Dashboard Bundle v1** |
| A gauge, stat, table, log, trace, or topology panel | **Doctor Dashboard v1** (saved graphs only render line time series) |

Every new graph and dashboard must declare `partitions` — the partitions whose
data it affects. The server checks that the author has **maintainer or admin** on
every listed partition when storing a dashboard. Saved-graph bundles are
browser-local; their `partitions` field is metadata, so use a dashboard when the
graph must be shared under RBAC.

The canonical schemas are in `references/graph-spec.md`.

## 2. Discover the real data

Call tools before writing the query:

```text
doctor_metrics_names(search="http")
doctor_metrics_labels(match="http_server_request_duration_seconds_count")
doctor_metrics_query(promql="sum(rate(http_server_request_duration_seconds_count[5m]))")
```

Rules:

- Never invent a metric name. If discovery returns nothing, say so and ask for
  the service/partition or another signal.
- Confirm the query returns at least one series and one sample before declaring
  success.
- For a range graph, run `doctor_metrics_query` with `start`, `end`, and `step`
  and check that it returns a matrix with points.
- Use canonical labels where possible: `guardian_partition`, `guardian_intent`,
  `guardian_asset`, `guardian_component`, `service`, `namespace`, `pod`.
- For logs use `doctor_logql_schema` / `doctor_logql_label_values`, then
  `doctor_logql_query`.
- For traces use `doctor_traceql_tags` then `doctor_traceql_query`.

Determine the affected partitions from the user's request and the labels you
discovered (`guardian_partition` / `service`). Do not guess. If the request spans
multiple partitions, list all of them; storing the dashboard then requires
maintainer/admin on every partition. A `403` from Doctor means the caller lacks
that role — report it instead of retrying with different partitions. The agent's
metric tools do not expose role assignments, so do not claim a user has access
until the write succeeds.

## 3. Build the query

Choose the graph shape before the query:

| Goal | Query pattern |
|---|---|
| Request rate by service | `sum by (service) (rate(http_server_request_duration_seconds_count{guardian_partition="$partition"}[$__rate_interval]))` |
| 5xx ratio (%) | `100 * sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[$__rate_interval])) / clamp_min(sum(rate(http_server_request_duration_seconds_count[$__rate_interval])), 0.001)` |
| p95 latency | `histogram_quantile(0.95, sum by (le, service) (rate(http_server_request_duration_seconds_bucket[$__rate_interval])))` |
| CPU cores by service | `sum by (service) (rate(process_cpu_seconds_total{guardian_partition="$partition"}[$__rate_interval]))` |
| Resident memory | `sum by (service) (process_resident_memory_bytes{guardian_partition="$partition"})` |
| Top 10 endpoints | `topk(10, sum by (http_route) (rate(http_server_request_duration_seconds_count[$__rate_interval])))` |
| Is it up? | `up{guardian_partition="$partition"}` |

Rules:

- Use `$__rate_interval` in dashboard queries; use a concrete window such as
  `[5m]` in a saved graph if no dashboard variables are involved.
- Aggregate with `sum by (...)` instead of plotting every pod UID.
- Keep label matchers exact; do not invent label names.
- For a gauge, return a latest scalar or a small set of series from a percentage
  expression.

More patterns: `references/promql-patterns.md`.

## 4. Generate the file

Default filenames:

```text
graphs/<slug>-graph.yaml
graphs/<slug>-dashboard.yaml
```

Use `workspace_write` (or the editor) with the full file content. Do not use a
partial patch for a new graph file.

### Saved graph bundle

Use this for simple line charts:

```yaml
version: 1
exportedAt: "2026-11-05T08:00:00Z"
savedGraphs:
  - id: api-p95-latency
    title: API p95 latency
    query: >-
      histogram_quantile(0.95,
        sum by (le, service) (
          rate(http_server_request_duration_seconds_bucket{guardian_partition="agent"}[5m])
        )
      )
    rangeMinutes: 60
    refreshIntervalSec: 30
    createdAt: "2026-11-05T08:00:00Z"
    partitions: [agent]
savedDashboards: []
```

`partitions` is required for new graphs. Use every partition the query reads; do
not list a partition the user cannot modify unless it is intentional. `*` means
all partitions and is admin-only.

### Dashboard document

Use this when the user asks for a gauge, stat, table, logs, traces, topology, or
a multi-panel layout:

```yaml
version: doctor.dashboard/v1
id: api-overview
title: API overview
tenant: default
defaultTimeRange: 1h
refreshInterval: 30s
partitions: [agent]
layout:
  - id: request-rate
    title: Request rate
    kind: timeseries
    gridPos: {x: 0, y: 0, w: 6, h: 6}
    queries:
      - refId: A
        datasource: prometheus
        expr: >-
          sum by (service) (
            rate(http_server_request_duration_seconds_count{guardian_partition=~"$partition"}[$__rate_interval])
          )
        legendFormat: "{{service}}"
    visualization:
      displayMode: line
      unit: reqps
```

Full examples:

- `assets/example-graph-bundle.yaml`
- `assets/example-dashboard.yaml`
- `assets/example-single-graph.json`

## 5. Validate before answering

When running inside the agent, call the native tool:

```text
doctor_graph_validate(path="graphs/api-overview.yaml")
```

It accepts `path` (workspace-relative or absolute), or `content` with optional
`format: json|yaml` when the file has not been written yet.

From a terminal, run the bundled validator:

```bash
python3 .github/skills/create-doctor-graphs/scripts/validate_graph_file.py graphs/api-overview.yaml
```

It checks required fields, versions, IDs, widget kinds, datasources, grid bounds,
and duplicate IDs. Fix every error before presenting the file.

Also verify:

- [ ] Metric names came from `doctor_metrics_names`.
- [ ] The PromQL was executed with `doctor_metrics_query` and returned data.
- [ ] The time range in the graph matches the range you tested.
- [ ] A saved graph query returns a matrix, not a bare instant vector.
- [ ] A gauge panel returns a percentage (0–100) when `unit: percent`.
- [ ] `partitions` lists every affected partition; the author has maintainer/admin on each for dashboard storage.
- [ ] No secret values, tokens, or internal credentials are embedded.

## 6. Tell the user how to import it

**Saved graph bundle:** in Doctor open the partition view, find **Ad-hoc and
Persistent Dashboards**, click **Import JSON/YAML**, and select the file. Imports
are additive; duplicate title+query pairs are skipped. Saved graphs are stored in
the browser.

**Dashboard document:** open **Dashboards** → **Import** and select the file, or
POST it:

```bash
curl -sS -X POST "$DOCTOR_URL/v1/dashboards/import?tenant=default&mode=overwrite" \
  -H 'Content-Type: application/yaml' \
  --data-binary @graphs/api-overview.yaml
```

Built-in presets are read-only. Clone them before editing.

## 7. Response shape

Report concisely:

1. the file path you created,
2. what data it plots (metric, labels, time range),
3. the validation/query evidence (or why it is empty),
4. how to import it.

If the user only asked for the query, give the PromQL and the evidence; do not
invent a graph file they did not ask for.
