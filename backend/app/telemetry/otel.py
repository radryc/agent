from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)

_initialized = False
_instrumented = False
_tracer_provider = None
_meter_provider = None


def init_telemetry(component: str = "lagent-backend") -> None:
    global _initialized, _tracer_provider, _meter_provider
    if _initialized or not settings.otel_enabled:
        logger.info("OTEL disabled, skipping telemetry init")
        return

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "guardian.component": component,
            }
        )

        tracer_provider = TracerProvider(resource=resource)
        trace_exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=settings.otel_exporter_otlp_insecure,
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(tracer_provider)

        metric_exporter = OTLPMetricExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=settings.otel_exporter_otlp_insecure,
        )
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=settings.otel_metric_export_interval_ms,
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[metric_reader],
        )
        metrics.set_meter_provider(meter_provider)

        _tracer_provider = tracer_provider
        _meter_provider = meter_provider

        _initialized = True
        logger.info(
            "OTEL initialized: exporting traces and metrics to %s",
            settings.otel_exporter_otlp_endpoint,
        )
    except Exception:
        logger.exception("Failed to initialize OTEL telemetry")


def instrument_app(app) -> None:
    global _instrumented
    if not settings.otel_enabled or not _initialized or _instrumented:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=_tracer_provider,
            meter_provider=_meter_provider,
        )
        _instrumented = True
        logger.info("FastAPI OTEL instrumentation applied")
    except Exception:
        logger.exception("Failed to instrument FastAPI")


def get_tracer(name: str = "lagent"):
    from opentelemetry import trace

    return trace.get_tracer(name)
