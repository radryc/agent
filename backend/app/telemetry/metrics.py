"""Prometheus metrics for the lagent backend.

The OTel SDK exports metrics over OTLP, but the backend also needs a direct
scrape endpoint for perf runs (and for when the collector is unavailable).
"""
from __future__ import annotations

import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "agent_http_requests_total",
    "HTTP requests handled by the lagent backend.",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "agent_http_request_duration_seconds",
    "HTTP request latency in the lagent backend.",
    ["method", "path"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LLM_READY = Gauge(
    "agent_llm_ready",
    "1 when the configured LLM provider is reachable, 0 otherwise.",
)

_SKIP_PATHS = {"/metrics", "/healthz", "/ready"}


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)
        start = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = time.monotonic() - start
            path = request.url.path
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            HTTP_DURATION.labels(request.method, path).observe(elapsed)


def render_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
