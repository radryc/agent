import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agent import loop  # noqa: E402


class ToolCallExtractionTest(unittest.TestCase):
    def test_extracts_every_json_call_in_order(self):
        text = (
            "I'll pull the partition and the events.\n\n"
            '{"tool": "guardian_partition_detail", "args": {"partition": "monofs"}}\n\n'
            '{"tool": "doctor_event_analysis", "args": {"partition": "monofs"}}'
        )
        calls = loop._extract_tool_calls(text)
        self.assertEqual(
            [call["tool"] for call in calls],
            ["guardian_partition_detail", "doctor_event_analysis"],
        )
        self.assertEqual(calls[0]["args"]["partition"], "monofs")

    def test_extracts_every_dsml_call(self):
        text = (
            '<｜｜DSML｜｜invoke name="doctor_logs_search">'
            '<｜｜DSML｜｜parameter name="partition">monofs</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
            '<｜｜DSML｜｜invoke name="doctor_traces_recent">'
            '<｜｜DSML｜｜parameter name="limit">5</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
        )
        calls = loop._extract_tool_calls(text)
        self.assertEqual(
            [call["tool"] for call in calls],
            ["doctor_logs_search", "doctor_traces_recent"],
        )
        self.assertEqual(calls[1]["args"]["limit"], 5)

    def test_strip_tool_calls_keeps_prose(self):
        text = 'Checking.\n{"tool": "guardian_overview", "args": {}}\nDone.'
        self.assertEqual(loop._strip_tool_calls(text), "Checking.\n\nDone.")


class ToolUsePolicyTest(unittest.TestCase):
    def test_telemetry_requests_require_tools(self):
        for message in (
            "list available partitions",
            "check latest errors in monofs partition",
            "check logs and traces",
            "what is the p99 latency?",
            "is everything healthy?",
        ):
            self.assertTrue(loop._requires_tool_use(message), message)

    def test_small_talk_does_not_require_tools(self):
        for message in ("hello", "thanks", "write a haiku about rust"):
            self.assertFalse(loop._requires_tool_use(message), message)

    def test_metric_value_intent_detection(self):
        self.assertTrue(
            loop._metric_value_requested(
                "what is the CPU usage for the monofs nodes? show the actual values"
            )
        )
        self.assertTrue(loop._metric_value_requested("how much memory is monofs using?"))
        self.assertFalse(loop._metric_value_requested("what CPU metrics exist for monofs?"))
        self.assertFalse(loop._metric_value_requested("list the metric catalog"))

    def test_empty_result_detection(self):
        self.assertTrue(loop._tool_result_is_empty('{"returned": 0, "traces": []}'))
        self.assertTrue(loop._tool_result_is_empty('{"results": [], "returned": 0}'))
        self.assertFalse(loop._tool_result_is_empty('{"returned": 1, "results": [{"a": 1}]}'))
        self.assertFalse(loop._tool_result_is_empty("Error: unauthorized"))

    def test_formatted_results_call_out_empty_tools(self):
        formatted = loop._format_tool_results(
            [
                ("doctor_logs_search", '{"returned": 0, "results": []}'),
                ("guardian_overview", '{"summary": {"partitions": 9}}'),
            ]
        )
        self.assertIn("Empty results: doctor_logs_search", formatted)
        self.assertIn("never invent", formatted.lower())


class LogQLNormalizationTest(unittest.TestCase):
    def test_level_equality_becomes_case_insensitive_regex(self):
        from app.agent.tools import _normalize_logql_severity

        self.assertEqual(
            _normalize_logql_severity('{guardian_partition="monofs", level="warn"}'),
            '{guardian_partition="monofs", level=~"(?i)warn"}',
        )
        self.assertEqual(
            _normalize_logql_severity('{severity_text="INFO"}'),
            '{severity_text=~"(?i)INFO"}',
        )

    def test_regex_matcher_is_unchanged(self):
        from app.agent.tools import _normalize_logql_severity

        query = '{level=~"(?i)warn|error"}'
        self.assertEqual(_normalize_logql_severity(query), query)


class LogQLHintTest(unittest.IsolatedAsyncioTestCase):
    async def test_severity_body_filter_gets_label_hint(self):
        import json
        from unittest.mock import patch

        from app.agent import tools as agent_tools

        empty = json.dumps(
            {"status": "success", "data": {"resultType": "streams", "result": []}}
        )

        async def fake_get(_path):
            return empty

        with patch.object(agent_tools, "_doctor_get", fake_get):
            out = await agent_tools._tool_doctor_logql_query(
                {"query": '{guardian_partition="monofs"} |~ "(?i)warn"'}
            )

        payload = json.loads(out)
        self.assertIn("hint", payload)
        self.assertIn('level=~"(?i)warn"', payload["hint"])

    async def test_level_label_query_does_not_get_hint(self):
        import json
        from unittest.mock import patch

        from app.agent import tools as agent_tools

        empty = json.dumps(
            {"status": "success", "data": {"resultType": "streams", "result": []}}
        )

        async def fake_get(_path):
            return empty

        with patch.object(agent_tools, "_doctor_get", fake_get):
            out = await agent_tools._tool_doctor_logql_query(
                {"query": '{guardian_partition="monofs", level="warn"}'}
            )

        self.assertNotIn("hint", json.loads(out))


class ToolLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_executes_every_call_in_one_round(self):
        responses = [
            (
                '{"tool": "doctor_logs_search", "args": {"partition": "monofs"}}\n'
                '{"tool": "doctor_traces_recent", "args": {"partition": "monofs"}}'
            ),
            "final answer",
        ]
        executed: list[str] = []

        async def fake_chat(_messages, **_kwargs):
            return responses.pop(0)

        async def fake_exec(name, _args, allowed=None):
            executed.append(name)
            return '{"returned": 0, "results": []}'

        from unittest.mock import patch

        with patch.object(loop, "chat_completion", fake_chat), patch.object(
            loop, "execute_tool", fake_exec
        ):
            result = await loop._run_tool_loop(
                [{"role": "user", "content": "check logs and traces"}],
                require_tools=True,
            )

        self.assertEqual(executed, ["doctor_logs_search", "doctor_traces_recent"])
        self.assertEqual(result, "final answer")

    async def test_metric_value_request_runs_query_after_catalog(self):
        responses = [
            '{"tool": "doctor_metrics_names", "args": {"search": "cpu"}}',
            '{"tool": "doctor_metrics_query", "args": {"promql": "topk(2, k8s_top_pod_cpu_usage_millicores)"}}',
            "CPU values found.",
        ]
        executed: list[str] = []

        async def fake_chat(_messages, **_kwargs):
            return responses.pop(0)

        async def fake_exec(name, _args, allowed=None):
            executed.append(name)
            return '{"series": 1, "results": [{"value": "392"}]}'

        from unittest.mock import patch

        with patch.object(loop, "chat_completion", fake_chat), patch.object(
            loop, "execute_tool", fake_exec
        ):
            result = await loop._run_tool_loop(
                [{"role": "user", "content": "show the CPU value"}],
                require_tools=True,
                metric_value=True,
            )

        self.assertEqual(executed, ["doctor_metrics_names", "doctor_metrics_query"])
        self.assertEqual(result, "CPU values found.")

    async def test_metric_value_auto_queries_first_catalog_metric(self):
        responses = [
            '{"tool": "doctor_metrics_names", "args": {"search": "cpu"}}',
            "The auto-query returned a real value.",
        ]
        executed: list[str] = []

        async def fake_chat(_messages, **_kwargs):
            return responses.pop(0)

        async def fake_exec(name, args, allowed=None):
            executed.append(name)
            if name == "doctor_metrics_names":
                return '{"metric_names": ["k8s_top_pod_cpu_usage_millicores"]}'
            if name == "doctor_metrics_query":
                return '{"series": 1, "results": [{"labels": {"k8s_pod_name": "node-a"}, "value": "392"}]}'
            raise AssertionError(f"unexpected tool {name} {args}")

        from unittest.mock import patch

        with patch.object(loop, "chat_completion", fake_chat), patch.object(
            loop, "execute_tool", fake_exec
        ):
            result = await loop._run_tool_loop(
                [{"role": "user", "content": "show the CPU value for monofs"}],
                require_tools=True,
                metric_value=True,
                partition_hint="monofs",
            )

        self.assertEqual(executed, ["doctor_metrics_names", "doctor_metrics_query"])
        self.assertEqual(result, "The auto-query returned a real value.")

    async def test_nudges_once_when_telemetry_answer_has_no_tool(self):
        responses = ["I think it is fine.", "Still fine after the nudge."]
        calls = 0

        async def fake_chat(_messages, **_kwargs):
            nonlocal calls
            calls += 1
            return responses.pop(0)

        from unittest.mock import patch

        with patch.object(loop, "chat_completion", fake_chat), patch.object(
            loop, "execute_tool", assert_not_called
        ):
            result = await loop._run_tool_loop(
                [{"role": "user", "content": "check logs"}],
                require_tools=True,
            )

        self.assertEqual(calls, 2)
        self.assertEqual(result, "Still fine after the nudge.")


async def assert_not_called(*_args, **_kwargs):
    raise AssertionError("execute_tool should not be called without a tool call")


class NativeQueryCompactionTest(unittest.TestCase):
    def test_loki_streams_are_compacted(self):
        import json

        from app.agent.tools import _compact_loki_payload

        raw = json.dumps(
            {
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"level": "warn", "guardian_partition": "monofs"},
                            "values": [["1", "read: metadata not found"]],
                        }
                    ],
                },
            }
        )
        out = json.loads(_compact_loki_payload(raw))
        self.assertEqual(out["streams"], 1)
        self.assertEqual(out["lines"], 1)
        self.assertEqual(out["results"][0]["line"], "read: metadata not found")
        self.assertEqual(out["results"][0]["labels"]["level"], "warn")

    def test_tempo_search_is_compacted(self):
        import json

        from app.agent.tools import _compact_tempo_search

        raw = json.dumps(
            {
                "traces": [
                    {
                        "traceID": "abc",
                        "durationMs": 12,
                        "spanSets": [{"matched": 2, "spans": [{"name": "CheckCache"}]}],
                    }
                ]
            }
        )
        out = json.loads(_compact_tempo_search(raw))
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["traces"][0]["trace_id"], "abc")
        self.assertEqual(out["traces"][0]["matched_spans"], 2)
        self.assertEqual(out["traces"][0]["span_names"], ["CheckCache"])

    def test_traceql_metrics_are_compacted(self):
        import json

        from app.agent.tools import _compact_traceql_metrics

        raw = json.dumps(
            {
                "series": [
                    {
                        "labels": [{"key": "service", "value": "monofs-router"}],
                        "samples": [{"value": 1}, {"value": 2}],
                    }
                ]
            }
        )
        out = json.loads(_compact_traceql_metrics(raw))
        self.assertEqual(out["series"], 1)
        self.assertEqual(out["results"][0]["labels"]["service"], "monofs-router")
        self.assertEqual(out["results"][0]["points"], 2)


class LogRecordFilterTest(unittest.TestCase):
    def test_matches_short_component_and_asset_names(self):
        from app.agent.tools import _log_record_matches

        record = {
            "service": "monofs.monofs-storage.router-a",
            "attributes": {
                "guardian.component": "monofs-router",
                "guardian.asset": "router-a",
            },
        }
        self.assertTrue(_log_record_matches(record, service="monofs-router"))
        self.assertTrue(_log_record_matches(record, component="monofs-router"))
        self.assertTrue(_log_record_matches(record, asset="router-a"))
        self.assertFalse(_log_record_matches(record, component="monofs-server"))
        self.assertFalse(_log_record_matches(record, asset="node-a"))


class MetricCompactionTest(unittest.TestCase):
    def test_drops_resource_noise(self):
        import json

        from app.agent.tools import _compact_metric_payload

        raw = json.dumps(
            {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {
                                "__name__": "k8s_top_pod_cpu_usage_millicores",
                                "k8s_pod_name": "node-a",
                                "service": "noise",
                                "telemetry_sdk_language": "go",
                            },
                            "value": [1, "392"],
                        }
                    ],
                },
            }
        )
        out = json.loads(_compact_metric_payload(raw))
        self.assertEqual(out["series"], 1)
        self.assertEqual(
            out["results"][0]["labels"],
            {
                "__name__": "k8s_top_pod_cpu_usage_millicores",
                "k8s_pod_name": "node-a",
            },
        )
        self.assertEqual(out["results"][0]["value"], [1, "392"])


class TraceStatusTest(unittest.TestCase):
    def test_error_status_detection(self):
        from app.agent.tools import _span_is_error

        self.assertTrue(_span_is_error({"status_code": "STATUS_CODE_ERROR"}))
        self.assertTrue(
            _span_is_error(
                {"status_code": "STATUS_CODE_UNSET", "attributes": {"rpc.response.status_code": "UNAVAILABLE"}}
            )
        )
        self.assertTrue(
            _span_is_error(
                {"status_code": "STATUS_CODE_UNSET", "attributes": {"http.status_code": 503}}
            )
        )
        self.assertFalse(_span_is_error({"status_code": "STATUS_CODE_UNSET"}))
        self.assertFalse(
            _span_is_error(
                {"status_code": "STATUS_CODE_UNSET", "attributes": {"rpc.response.status_code": "OK"}}
            )
        )


if __name__ == "__main__":
    unittest.main()
