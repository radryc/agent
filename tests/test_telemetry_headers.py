from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.telemetry import otel  # noqa: E402

_HEADER_VARS = (
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
)

_TRACE_VARS = (
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
)
_METRIC_VARS = (
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
)


def _env(values: dict[str, str]):
    """Patch the OTLP header variables and clear any not supplied."""
    saved = {key: os.environ.get(key) for key in _HEADER_VARS}
    for key in _HEADER_VARS:
        os.environ.pop(key, None)
    os.environ.update(values)
    return saved


def _restore(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class OTLPHeadersTest(unittest.TestCase):
    def _parse(self, env: dict[str, str], env_vars):
        saved = _env(env)
        try:
            return otel.otlp_headers(*env_vars)
        finally:
            _restore(saved)

    def test_generic_headers_are_parsed_and_unquoted(self):
        env = {
            "OTEL_EXPORTER_OTLP_HEADERS": (
                "authorization=Bearer%20shared-token,x-tenant=default"
            )
        }
        expected = {
            "authorization": "Bearer shared-token",
            "x-tenant": "default",
        }
        self.assertEqual(self._parse(env, _TRACE_VARS), expected)
        self.assertEqual(self._parse(env, _METRIC_VARS), expected)

    def test_signal_specific_headers_override_generic_keys(self):
        env = {
            "OTEL_EXPORTER_OTLP_HEADERS": (
                "authorization=Bearer%20generic,x-tenant=default"
            ),
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": (
                "authorization=Bearer%20traces,x-signal=trace"
            ),
        }
        self.assertEqual(
            self._parse(env, _TRACE_VARS),
            {
                "authorization": "Bearer traces",
                "x-tenant": "default",
                "x-signal": "trace",
            },
        )
        self.assertEqual(
            self._parse(env, _METRIC_VARS),
            {
                "authorization": "Bearer generic",
                "x-tenant": "default",
            },
        )

    def test_signal_specific_headers_work_without_generic(self):
        env = {
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS": (
                "authorization=Bearer%20metrics"
            )
        }
        self.assertEqual(
            self._parse(env, _METRIC_VARS),
            {"authorization": "Bearer metrics"},
        )

    def test_malformed_pairs_are_ignored(self):
        env = {
            "OTEL_EXPORTER_OTLP_HEADERS": (
                "good=value,missing-separator,=no-key,no-value="
            )
        }
        self.assertEqual(
            self._parse(env, _TRACE_VARS),
            {"good": "value", "no-value": ""},
        )

    def test_empty_environment_returns_empty_mapping(self):
        self.assertEqual(self._parse({}, _TRACE_VARS), {})
        self.assertEqual(self._parse({}, _METRIC_VARS), {})


if __name__ == "__main__":
    unittest.main()
