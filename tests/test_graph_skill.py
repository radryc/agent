import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".github" / "skills" / "create-doctor-graphs"
VALIDATOR = SKILL_DIR / "scripts" / "validate_graph_file.py"

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.agent import tools as agent_tools  # noqa: E402


class GraphSpecValidatorTest(unittest.TestCase):
    def run_validator(self, *paths: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), *map(str, paths)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_example_assets_validate(self):
        result = self.run_validator(
            SKILL_DIR / "assets" / "example-graph-bundle.yaml",
            SKILL_DIR / "assets" / "example-dashboard.yaml",
            SKILL_DIR / "assets" / "example-single-graph.json",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("saved-graph-bundle", result.stdout)
        self.assertIn("dashboard", result.stdout)
        self.assertIn("saved-graph", result.stdout)

    def test_invalid_dashboard_fails(self):
        document = {
            "version": "doctor.dashboard/v1",
            "id": "bad dashboard",
            "title": "Bad",
            "tenant": "default",
            "layout": [
                {
                    "id": "panel",
                    "title": "Panel",
                    "kind": "timeseries",
                    "gridPos": {"x": 10, "y": 0, "w": 6, "h": 6},
                    "queries": [
                        {
                            "refId": "A",
                            "datasource": "not-a-datasource",
                            "expr": "up",
                        }
                    ],
                }
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(document, handle)
            path = Path(handle.name)
        try:
            result = self.run_validator(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("exceeds the 12-column grid", result.stderr)
        self.assertIn("datasource must be one of", result.stderr)

    def test_invalid_saved_bundle_fails(self):
        document = {"version": 1, "savedGraphs": [{"title": "", "query": ""}], "savedDashboards": []}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(document, handle)
            path = Path(handle.name)
        try:
            result = self.run_validator(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing non-empty title", result.stderr)
        self.assertIn("missing non-empty query", result.stderr)
        self.assertIn("partitions is required", result.stderr)

    def test_native_validator_tool_accepts_content(self):
        document = {
            "version": 1,
            "savedGraphs": [{"title": "Up", "query": "up", "rangeMinutes": 60, "partitions": ["agent"]}],
            "savedDashboards": [],
        }
        result = asyncio.run(
            agent_tools._tool_doctor_graph_validate(
                {"content": json.dumps(document), "format": "json"}
            )
        )
        self.assertIn("OK (saved-graph-bundle)", result, result)

    def test_native_validator_tool_reports_errors(self):
        document = {
            "version": "doctor.dashboard/v1",
            "id": "broken",
            "title": "Broken",
            "tenant": "default",
            "layout": [
                {
                    "id": "panel",
                    "title": "Panel",
                    "kind": "not-a-kind",
                    "gridPos": {"x": 0, "y": 0, "w": 3, "h": 3},
                    "queries": [],
                }
            ],
        }
        result = asyncio.run(
            agent_tools._tool_doctor_graph_validate({"content": json.dumps(document)})
        )
        self.assertIn("INVALID", result, result)
        self.assertIn("kind must be one of", result, result)


if __name__ == "__main__":
    unittest.main()
