import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
