# PromQL patterns for Doctor graphs

Use these as starting points, not as proof that a metric exists. Always discover
metric names with `doctor_metrics_names` and confirm the query with
`doctor_metrics_query` before writing a graph.

## Range vs instant

- Range graph: `doctor_metrics_query(promql=..., start=..., end=..., step=...)`
  must return a matrix with points.
- Gauge/stat panel: an instant query is acceptable, but the value should be a
  single scalar or a small number of series.
- Saved graphs render line time series. Use a range function, not a bare counter.

## Counters

```promql
# Per-second rate, grouped
sum by (service) (rate(http_server_request_duration_seconds_count{guardian_partition="agent"}[$__rate_interval]))

# Total rate
sum(rate(process_cpu_seconds_total{guardian_partition="agent"}[$__rate_interval]))
```

Use `rate()` / `increase()` only on counters. A metric ending in `_total`,
`_count`, or `_sum` is usually a counter.

## Histograms

```promql
# p95 by service
histogram_quantile(0.95,
  sum by (le, service) (
    rate(http_server_request_duration_seconds_bucket{guardian_partition="agent"}[$__rate_interval])
  )
)

# Average request duration
sum(rate(http_server_request_duration_seconds_sum[$__rate_interval]))
/
clamp_min(sum(rate(http_server_request_duration_seconds_count[$__rate_interval])), 0.001)
```

Always `sum by (le, ...)` before `histogram_quantile`. Forgetting `le` produces
nonsense.

## Gauges

```promql
sum(process_resident_memory_bytes{guardian_partition="agent"})
max(container_memory_working_set_bytes{guardian_partition="agent"})
```

Gauges do not use `rate()`. Use `avg_over_time()` or `max_over_time()` when you
need smoothing.

## Error ratio as a percentage (0–100)

```promql
100 *
sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"5..",guardian_partition="agent"}[$__rate_interval]))
/
clamp_min(
  sum(rate(http_server_request_duration_seconds_count{guardian_partition="agent"}[$__rate_interval])),
  0.001
)
```

Use `clamp_min` to avoid division by zero. `unit: percent` appends `%` without
multiplying, so the expression must already be 0–100.

## Availability

```promql
up{guardian_partition="agent"}
```

For a percentage:

```promql
100 * avg(up{guardian_partition="agent"})
```

## Ranking

```promql
topk(10, sum by (http_route) (rate(http_server_request_duration_seconds_count{guardian_partition="agent"}[$__rate_interval])))
```

Use `topk` or `bottomk` for `toplist` and table panels. Do not return unbounded
cardinality.

## Grouping and labels

| Goal | Expression |
|---|---|
| One line per service | `sum by (service) (...)` |
| One line per partition | `sum by (guardian_partition) (...)` |
| One line per intent | `sum by (guardian_intent) (...)` |
| Total across all series | `sum(...)` |
| Keep a dimension | include it in `by (...)` |

Useful Doctor labels: `guardian_partition`, `guardian_intent`, `guardian_asset`,
`guardian_component`, `service`, `namespace`, `pod`, `node`.

## Time windows

- `$__rate_interval` in dashboards — safe window derived from the selected range.
- `[5m]` in saved graphs — literal window.
- Prefer at least two scrape intervals. Very short windows produce gaps.

## Empty results

If a query returns no series:

1. check the metric name with `doctor_metrics_names`
2. remove one label matcher at a time
3. try `up{guardian_partition="..."}` to confirm the target is reporting
4. widen the time range
5. report "no series returned" and what you checked

Do not replace an empty result with an invented example value.

## legendFormat

Use label templates:

```yaml
legendFormat: "{{service}}"
legendFormat: "{{guardian_partition}}/{{service}}"
```

Only reference labels present in the query result. If the query aggregates them
away, they are not available.
