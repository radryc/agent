import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agent import sre_flow  # noqa: E402
from app.agent import loop as loop_mod  # noqa: E402
from app.agent import specialists  # noqa: E402
from app.agent.contracts import (  # noqa: E402
    EvidenceRecord,
    IncidentVerdict,
    TelemetryReport,
    TopologyReport,
)
from app.agent.golden_signals import (  # noqa: E402
    analyze_golden_signals,
    format_golden_signals,
    gaps_from_coverage,
)
from app.agent.guardrails import (  # noqa: E402
    apply_nightwatch_policy,
    metric_catalog_followup_required,
    zero_fabrication_ok,
)
from app.agent.nightwatch_bridge import _assessment, _reports_from_pack  # noqa: E402
from app.agent.reports import (  # noqa: E402
    ToolExecution,
    build_steve_report,
    build_telemetry_report,
    build_topology_report,
)


class RouteTest(unittest.TestCase):
    def test_ops_routes_to_triage(self):
        self.assertEqual(
            sre_flow.classify_route("check the logs and traces for monofs"), "triage"
        )

    def test_code_routes_to_steve(self):
        self.assertEqual(
            sre_flow.classify_route("fix the handler bug in the repo"), "code_fix"
        )

    def test_mixed_routes_to_collab(self):
        self.assertEqual(
            sre_flow.classify_route(
                "check the failing trace for monofs and patch the handler"
            ),
            "crew_collab",
        )

    def test_mutation_ignores_questions(self):
        self.assertFalse(sre_flow.has_mutation_signal("why did the deployment fail?"))
        self.assertTrue(sre_flow.has_mutation_signal("reconcile partition=monofs"))

    def test_manual_steve_stays_code(self):
        self.assertEqual(
            sre_flow.classify_route("what is the status of things", "steve"), "code_fix"
        )


class GuardrailTest(unittest.TestCase):
    def test_metric_catalog_followup(self):
        self.assertTrue(
            metric_catalog_followup_required(
                "show the CPU value", ["doctor_metrics_names"]
            )
        )
        self.assertFalse(
            metric_catalog_followup_required(
                "show the CPU value",
                ["doctor_metrics_names", "doctor_metrics_query"],
            )
        )
        self.assertFalse(
            metric_catalog_followup_required(
                "what metrics exist?", ["doctor_metrics_names"]
            )
        )

    def test_zero_fabrication(self):
        ok, _ = zero_fabrication_ok(
            "No data was returned by the tools.", ['{"returned": 0, "results": []}']
        )
        self.assertTrue(ok)
        ok, reason = zero_fabrication_ok(
            "CPU is at 92% on pod-a.", ['{"returned": 0, "results": []}']
        )
        self.assertFalse(ok)
        self.assertIn("Every tool result was empty", reason)

    def test_nightwatch_cross_partition_and_low_confidence(self):
        verdict = IncidentVerdict(
            summary="s",
            root_cause_partition="doctor",
            root_cause_service="query",
            confidence=0.9,
            recommended_action="reconcile",
        )
        verdict = apply_nightwatch_policy(verdict, "monofs")
        self.assertTrue(verdict.cross_partition)
        self.assertTrue(verdict.escalate)
        self.assertEqual(verdict.recommended_action, "handoff")
        self.assertEqual(verdict.escalation_target, "doctor")

        low = IncidentVerdict(
            summary="s",
            root_cause_partition="monofs",
            root_cause_service="router",
            confidence=0.2,
            recommended_action="reconcile",
        )
        low = apply_nightwatch_policy(low, "monofs")
        self.assertTrue(low.escalate)
        self.assertEqual(low.recommended_action, "escalate")


class ReportTest(unittest.TestCase):
    def test_telemetry_report_collects_signals_and_gaps(self):
        executions = [
            ToolExecution(
                name="doctor_metrics_query",
                args={"promql": "rate(errors_total[5m])"},
                result='{"series": 1, "returned": 1, "results": [{"labels": {}, "value": [1, "4"]}]}',
            ),
            ToolExecution(
                name="doctor_logs_search",
                args={"partition": "monofs"},
                result=(
                    '{"results": [{"line": "ERROR boom", '
                    '"labels": {"level": "error"}}]}'
                ),
            ),
            ToolExecution(
                name="doctor_traces_recent",
                args={"partition": "monofs"},
                result=(
                    '{"error_summary": {"traces_with_errors": 1, "error_spans": 2},'
                    ' "traces": [{"trace_id": "abc", "error_count": 1,'
                    ' "error_spans": [{"name": "GET /"}]}]}'
                ),
            ),
            ToolExecution(
                name="doctor_logql_query",
                args={"query": "{}"},
                result='{"returned": 0, "results": []}',
            ),
        ]
        report = build_telemetry_report("monofs", executions)
        self.assertEqual(report.metrics_queried, ["rate(errors_total[5m])"])
        self.assertEqual(report.anomalous_metrics, ["rate(errors_total[5m])"])
        self.assertTrue(any("ERROR boom" in line for line in report.error_logs))
        self.assertTrue(any("abc" in span for span in report.failing_spans))
        self.assertIn("doctor_logql_query", report.empty_signals)
        self.assertTrue(report.observability_gaps)
        self.assertEqual(len(report.evidence), 4)

    def test_topology_report(self):
        executions = [
            ToolExecution(
                name="guardian_partition_detail",
                args={"partition": "monofs"},
                result=(
                    '{"health": "degraded", "drifted_intents": ["router"],'
                    ' "failing_assets": ["router-a"],'
                    ' "cross_partition_targets": ["doctor"]}'
                ),
            )
        ]
        report = build_topology_report("monofs", executions)
        self.assertEqual(report.health_status, "degraded")
        self.assertEqual(report.drifted_intents, ["router"])
        self.assertEqual(report.failing_assets, ["router-a"])
        self.assertEqual(report.cross_partition_targets, ["doctor"])

    def test_steve_report_uses_write_paths_and_diff(self):
        executions = [
            ToolExecution(
                name="workspace_write",
                args={"path": "monofs/internal/fuse/commit.go"},
                result="Wrote 10 bytes",
            )
        ]
        report = build_steve_report(
            executions,
            "done",
            unified_diff="--- a/x\n+++ b/monofs/internal/fuse/commit.go\n",
        )
        self.assertEqual(
            report.files_changed, ["monofs/internal/fuse/commit.go"]
        )
        self.assertIn("+++ b/monofs", report.unified_diff)


class FlowRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_triage_persists_and_returns_verdict(self):
        verdict = IncidentVerdict(
            summary="router is sick",
            root_cause_partition="monofs",
            root_cause_service="router",
            confidence=0.8,
            recommended_action="notify",
            otto_response="Router errors are elevated.",
        )
        telemetry = TelemetryReport(partition="monofs")
        topology = TopologyReport(partition="monofs")
        with patch.object(sre_flow, "_history_context", AsyncMock(return_value="")), patch.object(
            sre_flow.session_mgr, "add_message", AsyncMock()
        ), patch.object(
            sre_flow,
            "_run_diagnostic",
            AsyncMock(return_value=(telemetry, topology, verdict, [])),
        ), patch.object(
            sre_flow, "_emit_text", AsyncMock()
        ):
            text = await sre_flow.run_turn(
                "check the logs and traces for monofs", "session-1"
            )
        self.assertIn("Router errors are elevated.", text)
        self.assertIn("Observability readiness", text)

    async def test_general_uses_legacy_with_scoped_tools(self):
        with patch.object(
            sre_flow, "_legacy_turn", AsyncMock(return_value="hello")
        ) as legacy:
            text = await sre_flow.run_turn("hello there", "session-1")
        self.assertEqual(text, "hello")
        self.assertEqual(legacy.await_args.kwargs["allowed_override"], sre_flow.GENERAL_TOOLS)

    async def test_code_route_runs_steve(self):
        with patch.object(sre_flow, "_history_context", AsyncMock(return_value="")), patch.object(
            sre_flow.session_mgr, "add_message", AsyncMock()
        ), patch.object(
            sre_flow,
            "_run_steve",
            AsyncMock(return_value=("patched", None)),
        ), patch.object(
            sre_flow, "_emit_text", AsyncMock()
        ):
            text = await sre_flow.run_turn(
                "fix the handler bug in the repo", "session-1", persona_id="steve"
            )
        self.assertEqual(text, "patched")

    async def test_mutation_emits_approval_and_resolves(self):
        events: list[dict] = []

        async def emit(payload):
            events.append(payload)

        with patch.object(sre_flow, "_history_context", AsyncMock(return_value="")), patch.object(
            sre_flow.session_mgr, "add_message", AsyncMock()
        ), patch.object(
            sre_flow, "_emit_text", AsyncMock()
        ):
            await sre_flow.run_turn(
                "reconcile partition=monofs", "session-1", emit=emit
            )
        approval = [item for item in events if item["type"] == "approval_required"]
        self.assertEqual(len(approval), 1)
        self.assertEqual(approval[0]["tool"], "guardian_reconcile")

        executed: list[tuple[str, dict]] = []

        async def fake_execute(name, args, allowed=None):
            executed.append((name, args))
            return "reconciled"

        with patch.object(sre_flow, "execute_tool", fake_execute), patch.object(
            sre_flow.session_mgr, "add_message", AsyncMock()
        ):
            result = await sre_flow.resolve_approval(approval[0]["id"], True)
        self.assertTrue(result["approved"])
        self.assertEqual(executed[0][0], "guardian_reconcile")
        self.assertTrue(executed[0][1]["confirm"])


class GoldenSignalTest(unittest.TestCase):
    @staticmethod
    def _report(queries: list[str], *, errors: bool = False) -> TelemetryReport:
        return TelemetryReport(
            partition="monofs",
            metrics_queried=queries,
            error_logs=["ERROR boom"] if errors else [],
            evidence=[
                EvidenceRecord(
                    kind="metric",
                    source="doctor",
                    query=f"doctor_metrics_query({{'promql': '{query}'}})",
                    result_summary="1 series",
                    empty=False,
                )
                for query in queries
            ],
        )

    def test_all_four_signals_covered(self):
        report = self._report(
            [
                "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))",
                "sum(rate(http_requests_total[5m]))",
                'sum(rate(http_requests_total{status=~"5.."}[5m]))',
                "max(container_memory_usage_bytes) by (pod)",
            ]
        )
        coverage = analyze_golden_signals(report)
        self.assertTrue(all(item.covered for item in coverage))
        self.assertEqual([item.signal for item in coverage], [
            "latency", "traffic", "errors", "saturation"
        ])

    def test_missing_signals_become_gaps(self):
        report = self._report(["sum(rate(http_requests_total[5m]))"], errors=True)
        coverage = analyze_golden_signals(report)
        by_signal = {item.signal: item for item in coverage}
        self.assertTrue(by_signal["traffic"].covered)
        self.assertTrue(by_signal["errors"].covered)
        self.assertFalse(by_signal["latency"].covered)
        self.assertFalse(by_signal["saturation"].covered)
        gaps = gaps_from_coverage(coverage, "monofs")
        self.assertEqual(len(gaps), 2)
        self.assertTrue(all(gap.category == "metrics" for gap in gaps))
        text = format_golden_signals(coverage)
        self.assertIn("2/4 golden signals covered", text)
        self.assertIn("**Latency**: **gap**", text)

    def test_long_http_server_request_queries_count_as_coverage(self):
        # Regression: evidence.query is truncated for display, which cut off
        # the closing quote of long PromQL and made executed signals look
        # unqueried. Coverage must come from the full metrics_queried labels.
        queries = [
            (
                'sum(rate(http_server_request_duration_count'
                '{guardian_partition="divb-shared"}[5m]))'
            ),
            (
                'sum(rate(http_server_request_duration_count'
                '{guardian_partition="divb-shared",'
                'http_response_status_code=~"5.."}[5m]))'
            ),
            (
                "max by (k8s_pod_name) (k8s_top_pod_cpu_usage_millicores"
                '{k8s_namespace_name="div-demo"})'
            ),
        ]
        report = TelemetryReport(partition="divb-shared", metrics_queried=queries)
        coverage = analyze_golden_signals(report)
        by_signal = {item.signal: item for item in coverage}
        self.assertTrue(by_signal["traffic"].covered)
        self.assertTrue(by_signal["errors"].covered)
        self.assertTrue(by_signal["saturation"].covered)
        # A request counter is traffic, not latency; only histograms/buckets
        # (or trace durations) count as latency coverage.
        self.assertFalse(by_signal["latency"].covered)


class VerdictRenderingTest(unittest.TestCase):
    def test_truncated_json_does_not_echo_raw_json(self):
        base = IncidentVerdict(
            summary="fallback summary",
            root_cause_partition="divb-shared",
            root_cause_service="processor",
            confidence=0.3,
        )
        raw = (
            '{"summary": "recovered summary", "root_cause_partition": '
            '"divb-shared", "root_cause_service": "processor", "rationale": '
            '"because the evidence is thin"'
        )
        verdict = sre_flow._merge_verdict(base, None, raw)
        self.assertEqual(verdict.summary, "recovered summary")
        self.assertEqual(verdict.rationale, "because the evidence is thin")
        self.assertFalse(verdict.otto_response.strip().startswith("{"))

    def test_unparseable_json_keeps_fallback_text(self):
        base = IncidentVerdict(
            summary="fallback summary",
            root_cause_partition="monofs",
            root_cause_service="router",
            confidence=0.2,
        )
        verdict = sre_flow._merge_verdict(base, None, '{"summary": "broken')
        self.assertEqual(verdict.otto_response, "")
        rendered = sre_flow._render_verdict_markdown(verdict)
        self.assertIn("fallback summary", rendered)
        self.assertIn("**Root cause**", rendered)
        self.assertNotIn('{"summary"', rendered)

    def test_render_includes_action_and_confidence(self):
        verdict = IncidentVerdict(
            summary="demo alert",
            root_cause_partition="divb-shared",
            root_cause_service="processor",
            confidence=0.35,
            recommended_action="escalate",
            recommendation_target="platform",
            escalate=True,
            escalation_target="platform-oncall",
            escalation_reason="trace backend timing out",
            rationale="no failing spans",
            falsification="real errors appear",
        )
        rendered = sre_flow._render_verdict_markdown(verdict)
        self.assertIn("demo alert", rendered)
        self.assertIn("35%", rendered)
        self.assertIn("`escalate`", rendered)
        self.assertIn("platform-oncall", rendered)
        self.assertIn("What would prove this wrong", rendered)


class SpecialistTest(unittest.IsolatedAsyncioTestCase):
    async def test_metric_followup_and_step_events(self):
        responses = [
            '{"tool": "doctor_metrics_names", "args": {"search": "cpu"}}',
            "CPU is 42.",
        ]
        executed: list[str] = []
        events: list[dict] = []

        async def fake_chat(_messages, **_kwargs):
            return responses.pop(0)

        async def fake_execute(name, _args, allowed=None):
            executed.append(name)
            if name == "doctor_metrics_names":
                return '{"metric_names": ["k8s_top_pod_cpu_usage_millicores"]}'
            if name == "doctor_metrics_query":
                return '{"series": 1, "returned": 1, "results": [{"value": "42"}]}'
            raise AssertionError(name)

        async def emit(payload):
            events.append(payload)

        with patch.object(loop_mod, "chat_completion", fake_chat), patch.object(
            specialists, "execute_tool", fake_execute
        ):
            result = await specialists.run_specialist(
                agent_id="telemetry_hound",
                system_prompt="test",
                allowed=sre_flow.TELEMETRY_TOOLS,
                user_message="show the CPU value for monofs",
                emit=emit,
                metric_value=True,
                partition_hint="monofs",
            )

        self.assertEqual(executed, ["doctor_metrics_names", "doctor_metrics_query"])
        self.assertEqual(result.text, "CPU is 42.")
        statuses = [event["status"] for event in events]
        self.assertIn("started", statuses)
        self.assertIn("tool_call", statuses)
        self.assertIn("tool_result", statuses)
        self.assertEqual(statuses[-1], "completed")


class NightwatchBridgeTest(unittest.TestCase):
    def _pack(self):
        return {
            "incident": {"id": "inc-1", "partition": "monofs", "title": "x"},
            "evidence": [
                {
                    "id": "ev-0",
                    "kind": "log",
                    "query": "logs",
                    "summary": "ERROR timeout",
                },
                {
                    "id": "ev-1",
                    "kind": "topology",
                    "query": "topology",
                    "summary": "router-a failing",
                },
            ],
            "topology_candidates": [
                {"partition": "monofs", "service": "router", "score": 0.7}
            ],
        }

    def test_reports_from_pack(self):
        telemetry, topology, candidates = _reports_from_pack(self._pack())
        self.assertEqual(telemetry.partition, "monofs")
        self.assertTrue(telemetry.error_logs)
        self.assertTrue(topology.recent_events)
        self.assertEqual(candidates[0].service, "router")

    def test_invalid_evidence_escalates(self):
        verdict = IncidentVerdict(
            summary="s",
            root_cause_partition="monofs",
            root_cause_service="router",
            confidence=0.9,
            recommended_action="notify",
        )
        payload = _assessment(verdict, self._pack())
        self.assertEqual(payload["evidence_ids"], [])
        self.assertEqual(payload["root_cause_candidate"]["confidence"], 0.0)
        self.assertTrue(payload["escalate"])
        self.assertEqual(payload["recommended_action"], "escalate")

    def test_patch_code_maps_to_notify_for_nightwatch(self):
        verdict = IncidentVerdict(
            summary="s",
            root_cause_partition="monofs",
            root_cause_service="router",
            confidence=0.9,
            recommended_action="patch_code",
            evidence_ids=["ev-0"],
        )
        payload = _assessment(verdict, self._pack())
        self.assertEqual(payload["recommended_action"], "notify")


class IncidentWindowTest(unittest.TestCase):
    def _runtime(self, **overrides):
        values = {
            "triage_lookback_minutes": 30,
            "triage_max_window_minutes": 120,
            **overrides,
        }
        return lambda key: values.get(key)

    def test_uses_alert_started_timestamp(self):
        now = datetime(2026, 11, 12, 8, 24, 30, tzinfo=timezone.utc)
        with patch.object(sre_flow, "get_runtime", self._runtime()):
            start, end = sre_flow._incident_window(
                "Alert\nStarted: 2026-11-12T08:23:48.894440026Z\nStatus: firing",
                now=now,
            )
        self.assertEqual(end, now)
        # A 5-minute pre-roll keeps the trigger that precedes the alert inside
        # the query window.
        self.assertEqual(
            start,
            datetime(2026, 11, 12, 8, 18, 48, 894440, tzinfo=timezone.utc),
        )

    def test_defaults_to_bounded_lookback(self):
        now = datetime(2026, 11, 12, 8, 24, 30, tzinfo=timezone.utc)
        with patch.object(sre_flow, "get_runtime", self._runtime()):
            start, end = sre_flow._incident_window("check partition=doctor", now=now)
        self.assertEqual(end, now)
        self.assertEqual(end - start, timedelta(minutes=30))

    def test_old_alert_is_clamped_to_max_window(self):
        now = datetime(2026, 11, 12, 8, 24, 30, tzinfo=timezone.utc)
        with patch.object(sre_flow, "get_runtime", self._runtime(triage_max_window_minutes=60)):
            start, _ = sre_flow._incident_window(
                "Started: 2026-11-10T00:00:00Z", now=now
            )
        self.assertEqual(now - start, timedelta(minutes=60))

    def test_triage_specs_are_incident_scoped_and_small(self):
        start = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
        end = datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc)
        specs = sre_flow._triage_telemetry_specs("doctor", start, end)
        names = [name for name, _ in specs]
        # The duplicate broad log scan is gone; the LogQL body filter covers it.
        self.assertNotIn("doctor_logs_search", names)
        self.assertIn("doctor_logql_query", names)
        trace_spec = next(args for name, args in specs if name == "doctor_traces_recent")
        self.assertEqual(trace_spec["limit"], 10)
        self.assertEqual(trace_spec["from"], "2026-11-12T08:00:00Z")
        self.assertEqual(trace_spec["to"], "2026-11-12T08:30:00Z")
        log_spec = next(args for name, args in specs if name == "doctor_logql_query")
        self.assertEqual(log_spec["start"], "2026-11-12T08:00:00Z")
        self.assertEqual(log_spec["end"], "2026-11-12T08:30:00Z")
        self.assertLessEqual(log_spec["limit"], 50)

    def test_topology_specs_bound_event_analysis(self):
        specs = sre_flow._triage_topology_specs("doctor")
        event_spec = next(args for name, args in specs if name == "doctor_event_analysis")
        self.assertLessEqual(event_spec["limit"], 50)


class EvidenceConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_queries_are_bounded(self):
        in_flight = 0
        peak = 0

        async def fake_execute(_name, _args, allowed=None):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.01)
                return '{"returned": 0, "results": []}'
            finally:
                in_flight -= 1

        specs = [("doctor_metrics_query", {"promql": f"m{i}"}) for i in range(8)]
        with patch.object(sre_flow, "get_runtime", lambda key: 3), patch.object(
            sre_flow, "execute_tool", fake_execute
        ):
            evidence = await sre_flow._collect_tool_evidence(
                agent_id="telemetry_hound",
                task_name="telemetry_hound.verify",
                specs=specs,
                allowed=sre_flow.TELEMETRY_TOOLS,
                emit=None,
            )
        self.assertEqual(len(evidence), 8)
        self.assertLessEqual(peak, 3)


class SynthesisPayloadTest(unittest.TestCase):
    def test_specialist_briefs_and_topology_events_survive_budget(self):
        telemetry = TelemetryReport(
            partition="doctor",
            metrics_queried=[f"metric_{i}" for i in range(30)],
            evidence=[
                EvidenceRecord(
                    kind="metric",
                    source="doctor",
                    query=f"metric_{i}",
                    result_summary="x" * 380,
                )
                for i in range(40)
            ],
        )
        topology = TopologyReport(
            partition="doctor",
            health_status="failing",
            recent_events=[
                "doctor_event_analysis: 2026-11-12T08:23:43Z | doctor | intent=query | "
                "APPLY failed: Deployment invalid: env[4].valueFrom may not be "
                "specified when value is not empty"
            ],
        )
        payload = sre_flow._synthesis_payload(
            message="triage doctor",
            partition="doctor",
            telemetry=telemetry,
            topology=topology,
            golden_signals=[],
            specialist_briefs={
                "telemetry_hound": "no error logs in window",
                "topology_auditor": "APPLY failed on env[4] and env[5]",
            },
            incident_window=(
                datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc),
            ),
        )
        self.assertIn("APPLY failed", payload["specialist_briefs"]["topology_auditor"])
        self.assertTrue(
            any("APPLY failed" in item for item in payload["topology"]["recent_events"])
        )
        self.assertEqual(payload["incident_window"]["start"], "2026-11-12T08:00:00Z")
        self.assertLessEqual(
            len(json.dumps(payload, default=str)), sre_flow._SYNTHESIS_MAX_CHARS
        )


class EvidenceOrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_otto_synthesizes_only_after_all_specialists(self):
        order: list[str] = []

        async def fake_specialist(**kwargs):
            order.append(f"specialist:{kwargs['agent_id']}")
            return specialists.SpecialistResult(
                agent_id=kwargs["agent_id"],
                text=f"{kwargs['agent_id']} brief",
            )

        async def fake_collect(**kwargs):
            order.append(f"collect:{kwargs['agent_id']}")
            return []

        async def fake_synthesize(**kwargs):
            order.append("synthesize")
            self.assertEqual(
                kwargs["specialist_briefs"]["topology_auditor"],
                "topology_auditor brief",
            )
            return IncidentVerdict(
                summary="ok",
                root_cause_partition="doctor",
                root_cause_service="query",
            )

        with patch.object(sre_flow, "run_specialist", fake_specialist), patch.object(
            sre_flow, "_collect_tool_evidence", fake_collect
        ), patch.object(sre_flow, "_synthesize_verdict", fake_synthesize):
            await sre_flow._run_diagnostic(
                message="triage partition=doctor",
                session_id="s",
                profile="local",
                emit=None,
                context="",
            )

        self.assertEqual(order[-1], "synthesize")
        self.assertIn("specialist:telemetry_hound", order)
        self.assertIn("specialist:topology_auditor", order)
        self.assertIn("collect:telemetry_hound", order)
        self.assertIn("collect:topology_auditor", order)
        self.assertLess(
            max(order.index(name) for name in order if name.startswith("specialist:")),
            order.index("synthesize"),
        )


class TopologyEventReportTest(unittest.TestCase):
    def test_event_analysis_failure_lines_and_health(self):
        executions = [
            ToolExecution(
                name="doctor_event_analysis",
                args={"partition": "doctor"},
                result=(
                    "Analyzed 50 Guardian events from Doctor for partition `doctor`.\n"
                    "Outcome counts: failure=2, success=48, unknown=0.\n"
                    "Recent failure signals:\n"
                    "- 2026-11-12T08:23:43Z | doctor | intent=query | "
                    'APPLY failed: Deployment.apps "doctor-query" is invalid: '
                    "env[4].valueFrom may not be specified when `value` is not empty\n"
                ),
            )
        ]
        report = build_topology_report("doctor", executions)
        self.assertEqual(report.health_status, "failing")
        self.assertTrue(
            any("APPLY failed" in item and "env[4]" in item for item in report.recent_events)
        )


if __name__ == "__main__":
    unittest.main()
