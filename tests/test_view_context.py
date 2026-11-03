import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agent import tools  # noqa: E402


class ViewContextTest(unittest.TestCase):
    def test_doctor_logs_route_has_starter_queries(self):
        label, _, starters = tools._resolve_view_starter("doctor", "/logs")
        self.assertEqual(label, "Logs")
        names = [name for name, _ in starters]
        self.assertIn("doctor_logs_services", names)
        self.assertIn("doctor_logs_search", names)

    def test_doctor_partition_route_uses_guardian_detail(self):
        label, _, starters = tools._resolve_view_starter(
            "doctor", "/partition/monofs/assets"
        )
        self.assertIn("monofs", label)
        self.assertEqual(starters[0][0], "guardian_partition_detail")
        self.assertEqual(starters[0][1], {"partition": "monofs"})

    def test_guardian_partition_query_is_scoped(self):
        label, _, starters = tools._resolve_view_starter(
            "guardian", "/?partition=agent&panel=topologyPanel"
        )
        self.assertIn("agent", label)
        self.assertEqual(starters[0][0], "guardian_partition_detail")
        self.assertEqual(starters[0][1], {"partition": "agent"})

    def test_lb_edge_route_uses_registry_tool(self):
        label, _, starters = tools._resolve_view_starter("lb-edge", "/")
        self.assertEqual(label, "Services")
        self.assertEqual(starters[0][0], "lb_edge_services")

    def test_handler_runs_starter_and_reports_context(self):
        calls: list[tuple[str, dict]] = []

        async def fake_execute(name, args=None, *, allowed=None):
            calls.append((name, args or {}))
            return "stub starter result"

        original = tools.execute_tool
        tools.execute_tool = fake_execute
        try:
            result = asyncio.run(
                tools._tool_strata_view_context(
                    {"context": "Doctor \u00b7 /traces"}
                )
            )
        finally:
            tools.execute_tool = original

        self.assertIn("View: Doctor", result)
        self.assertIn("starter result", result)
        self.assertEqual(calls[0][0], "doctor_traces_recent")


if __name__ == "__main__":
    unittest.main()
