---
name: query-agent-telemetry
description: "Use when the user asks to inspect, query, or diagnose traces, logs, or metrics for the agent partition or another Strata service, correlate telemetry with Guardian state, or verify observability after a deployment."
---

# Query telemetry for the agent partition

Use this skill when the user asks for logs, traces, metrics, latency, errors,
resource usage, or an incident timeline for `agent` (Lagent / Outage Otto /
Software Steve) or another Guardian-managed service.

## Goal

Produce evidence from Doctor (the telemetry query plane) and correlate it with
Guardian state. Do not guess at health or invent log lines.

## Telemetry model

- Doctor is the query frontend. It exposes Grafana-compatible Prometheus, Loki,
  and Tempo/Zipkin APIs.
- Guardian-managed workloads carry these labels:
  - `guardian_partition` — for example `agent`
  - `guardian_intent` — for example `agent`
  - `guardian_asset` — for example `lagent-backend`
  - `service` = `<partition>.<intent>.<asset>` — for example
    `agent.agent.lagent-backend`
- The `agent` partition normally emits:
  - `agent.agent.lagent-backend`
  - `agent.agent.lagent-frontend`
  - `agent.agent.lagent-llm`
- Platform services also use short names (`guardian`, `doctor`,
  `monofs-router`, `monofs-server`). Discover before filtering.
- Metrics endpoints are public by design. Logs and traces may require an
  authenticated Doctor session when `DOCTOR_ACCESS_MODE=enforce` or OIDC login
  is required.

## Native agent tools

When running inside the agent, prefer its native Doctor tools before shelling
out:

- `doctor_logs_services(partition="agent")`
- `doctor_logs_search(partition="agent", service="agent.agent.lagent-backend",
  query="error", from=..., to=...)`
- `doctor_traces_recent(partition="agent", service=..., from=..., to=...)`
- `doctor_trace_detail(trace_id=...)`
- `doctor_metrics_query(promql=...)`
- `doctor_guardian_events(partition="agent")`
- `agent_sso_status()`

The tools attach the agent's OIDC/SSO token automatically. Use the curl
recipes below when the tool parameters are insufficient or when working from a
terminal.

## 1. Resolve the Doctor query endpoint

From a machine with kubectl access:

```bash
kubectl -n doctor get svc | grep doctor-query
kubectl -n doctor port-forward svc/<doctor-query-service> 18080:18080
export DOCTOR_URL=http://127.0.0.1:18080
curl -sS "$DOCTOR_URL/healthz"
```

From inside the cluster, use the `doctor-query` service DNS name directly
(discover it with `kubectl -n doctor get svc`).

## 2. Fix the time window and scope

```bash
FROM=$(date -u -d '-15 minutes' +%Y-%m-%dT%H:%M:%SZ)
TO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
```

Use a wider window (`-1 hour`, `-24 hours`) for intermittent incidents. Always
report the window in the answer.

Check Guardian state first:

```bash
guardianctl -format json partition status --partition agent
guardianctl -format json intent status --partition agent --intent agent
guardianctl -format json partition get --partition agent
```

If `guardianctl` is not configured, use the Guardian API
(`$GUARDIAN_URL/api/overview`, `/api/partitions/agent`) or the Doctor Guardian
proxy endpoints in section 7.

## 3. Discover services and labels

```bash
curl -sS --get "$DOCTOR_URL/api/v1/label/service/values" \
  --data-urlencode 'tenant=default' | jq -r '.data[]?'

curl -sS --get "$DOCTOR_URL/api/v1/label/__name__/values" \
  --data-urlencode 'tenant=default' | jq -r '.data[]?' \
  | grep -E 'process_|http_|guardian_'
```

Filter by `guardian_partition="agent"` or by `service=~"agent\\..*"`.

## 4. Query metrics (PromQL)

Instant query:

```bash
curl -sS --get "$DOCTOR_URL/api/v1/query" \
  --data-urlencode 'tenant=default' \
  --data-urlencode 'query=up{guardian_partition="agent"}' | jq .
```

Range query:

```bash
curl -sS --get "$DOCTOR_URL/api/v1/query_range" \
  --data-urlencode 'tenant=default' \
  --data-urlencode 'query=sum by (service) (rate(process_cpu_seconds_total{service=~"agent\\..*"}[5m]))' \
  --data-urlencode "start=$FROM" \
  --data-urlencode "end=$TO" \
  --data-urlencode 'step=60s' | jq .
```

Useful queries:

```promql
# Is the workload reporting?
up{guardian_partition="agent"}

# CPU by service (cores)
sum by (service) (rate(process_cpu_seconds_total{service=~"agent\\..*"}[5m]))

# Resident memory by service
sum by (service) (process_resident_memory_bytes{service=~"agent\\..*"})

# HTTP request rate
sum by (service) (rate(http_server_request_duration_seconds_count{service=~"agent\\..*"}[5m]))

# 5xx rate
sum by (service) (rate(http_server_request_duration_seconds_count{service=~"agent\\..*", http_response_status_code=~"5.."}[5m]))

# p95 latency
histogram_quantile(0.95, sum by (le, service) (rate(http_server_request_duration_seconds_bucket{service=~"agent\\..*"}[5m])))
```

If the `agent` series are empty:

- confirm the pods are running: `kubectl -n agent get pods`
- confirm the workloads set `OTEL_ENABLED=true` and
  `OTEL_EXPORTER_OTLP_ENDPOINT`, and that the collector is reachable
- query collector health/labels (`doctor_component_up`, `otelcol_*`)
- fall back to `kubectl -n agent logs <pod> --since=15m` and
  `kubectl -n agent describe pod <pod>`
- do not claim metrics are healthy when no series exist; say "no series
  returned" and list what you checked

## 5. Query logs

Discover services:

```bash
curl -sS --get "$DOCTOR_URL/v1/logs/services" \
  --data-urlencode 'tenant=default' \
  --data-urlencode 'partition=agent' | jq .
```

Search:

```bash
curl -sS --get "$DOCTOR_URL/v1/logs/search" \
  --data-urlencode 'tenant=default' \
  --data-urlencode 'partition=agent' \
  --data-urlencode 'service=agent.agent.lagent-backend' \
  --data-urlencode 'q=error' \
  --data-urlencode "from=$FROM" \
  --data-urlencode "to=$TO" \
  --data-urlencode 'limit=100' | jq .
```

- `q` is a text filter, not LogQL.
- Omit `service` to search every service in the partition.
- Keep `limit` at or below 1000.
- Loki-compatible clients can use
  `/loki/api/v1/query_range?query={service="..."} |= "error"&start=<unix-ns>&end=<unix-ns>&limit=...`.

## 6. Query traces

Recent traces:

```bash
curl -sS --get "$DOCTOR_URL/v1/traces/recent" \
  --data-urlencode 'tenant=default' \
  --data-urlencode 'partition=agent' \
  --data-urlencode 'service=agent.agent.lagent-backend' \
  --data-urlencode "from=$FROM" \
  --data-urlencode "to=$TO" \
  --data-urlencode 'limit=50' | jq .
```

Full trace:

```bash
curl -sS --get "$DOCTOR_URL/v1/traces/<trace_id>" \
  --data-urlencode 'tenant=default' | jq .
```

Span search:

```bash
curl -sS --get "$DOCTOR_URL/v1/spans/search" \
  --data-urlencode 'tenant=default' \
  --data-urlencode 'service=agent.agent.lagent-backend' \
  --data-urlencode "from=$FROM" \
  --data-urlencode "to=$TO" \
  --data-urlencode 'limit=100' | jq .
```

Tempo-compatible search is available at `/api/search`; Zipkin clients can use
`/api/v2/traces?serviceName=...`.

## 7. Correlate with Guardian and the cluster

Doctor proxies Guardian context:

```bash
curl -sS --get "$DOCTOR_URL/v1/guardian/topology" \
  --data-urlencode 'tenant=default' --data-urlencode 'partition=agent'
curl -sS --get "$DOCTOR_URL/v1/guardian/events" \
  --data-urlencode 'tenant=default' --data-urlencode 'partition=agent' --data-urlencode 'limit=50'
curl -sS --get "$DOCTOR_URL/v1/guardian/health" \
  --data-urlencode 'tenant=default' --data-urlencode 'partition=agent' --data-urlencode 'window=1h'
```

Useful Guardian commands:

```bash
guardianctl -format json partition status --partition agent
guardianctl -format json intent status --partition agent --intent agent
guardianctl -format json intent describe --partition agent --intent agent
guardianctl -format json intent plan --partition agent
```

## Authentication

- `/api/v1/*` metrics endpoints are public.
- `/v1/logs/*` and `/v1/traces/*` can return `401 unauthorized` when Doctor
  requires login.
- Preferred: configure SSO on the agent backend with `AGENT_OIDC_*`. The agent
  obtains a client-credentials token automatically, or an operator can use
  **Settings -> SSO -> Sign in with SSO** (device flow).
- The agent's OIDC token is attached to Doctor and Guardian HTTP calls. Check
  it with `GET /api/auth/status`; a healthy response has
  `"authenticated": true`.
- If `DOCTOR_TOKEN` or `AGENT_OIDC_TOKEN` is set, send
  `Authorization: Bearer $DOCTOR_TOKEN`.
- Otherwise, sign in to the Doctor UI through the same port-forward and reuse
  the `doctor_session` cookie:

```bash
curl -sS --get "$DOCTOR_URL/v1/logs/search" \
  --cookie "doctor_session=$DOCTOR_SESSION" ...
```

- Never disable access enforcement or add a bypass just to make a query work.
- If you cannot authenticate, use `kubectl -n agent logs` and
  `kubectl -n agent describe` and report the limitation.

### SSO troubleshooting

See the `agent-sso-setup` skill for provider configuration and the full
troubleshooting checklist.

- `401` from Doctor usually means the token was issued by a different issuer
  than the one Doctor validates. For in-cluster Authentik use the in-cluster
  token URL, for example
  `http://authentik.authentik.svc.cluster.local:9000/application/o/token/`,
  not `http://localhost:9020/...`.
- Confirm the client ID, client secret, audience (`monofs`), and scope
  (`openid`).
- Confirm the device flow is enabled on the provider; the device
  authorization URL normally ends in `/application/o/device/`.
- Use `GET /api/auth/status` to see `configured`, `authenticated`, `source`,
  `subject`, `expires_at`, and the last `error`.

## Response shape

Report:

1. time window and query scope
2. Guardian state (healthy, drifted, failing)
3. metrics evidence, or "no series" and what was checked
4. log evidence with timestamps and service names
5. trace IDs, durations, and error spans
6. most likely cause and the next verification step

## Completion checklist

- [ ] Doctor endpoint resolved and reachable
- [ ] time window recorded
- [ ] Guardian state checked
- [ ] metrics queried, or absence explained
- [ ] logs searched for the affected service/partition
- [ ] traces inspected for errors or latency
- [ ] auth limitation reported if applicable
- [ ] conclusions cite evidence, not guesses
