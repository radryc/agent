import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import workspace  # noqa: E402
from app.agent.tools import execute_tool  # noqa: E402


class WorkspaceSandboxTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="monofs-workspace-test-")
        config._runtime_overrides["monofs_mount"] = self.tmp
        config._runtime_overrides["monofs_overlay"] = str(Path(self.tmp) / ".overlay")

    def tearDown(self):
        config._runtime_overrides.pop("monofs_mount", None)
        config._runtime_overrides.pop("monofs_overlay", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_write_read_roundtrip(self):
        result = await workspace.workspace_write(
            {"path": "repo/main.go", "content": "package main"}
        )
        self.assertIn("Wrote 12 bytes", result)
        self.assertIn("created", result)

        content = await workspace.workspace_read({"path": "repo/main.go"})
        self.assertEqual(content, "package main")

        listing = await workspace.workspace_list({"path": "repo"})
        self.assertIn("main.go", listing)

    async def test_write_update_reports_updated(self):
        await workspace.workspace_write({"path": "a.txt", "content": "one"})
        result = await workspace.workspace_write({"path": "a.txt", "content": "two"})
        self.assertIn("updated", result)

    async def test_escape_rejected_on_read(self):
        result = await workspace.workspace_read({"path": "../../etc/passwd"})
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("escapes", result)

    async def test_escape_rejected_on_write(self):
        result = await workspace.workspace_write(
            {"path": "/etc/passwd", "content": "nope"}
        )
        self.assertTrue(result.startswith("Error:"), result)
        self.assertIn("escapes", result)

    async def test_binary_read_refused(self):
        target = Path(self.tmp) / "blob.bin"
        target.write_bytes(b"\x00\x01\x02\xff\xfe")
        result = await workspace.workspace_read({"path": "blob.bin"})
        self.assertIn("binary", result)

    async def test_status_reports_not_mounted(self):
        result = await workspace.workspace_status({})
        self.assertIn("Active: no", result)


class MutationGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="monofs-gate-test-")
        config._runtime_overrides["monofs_mount"] = self.tmp
        config._runtime_overrides["monofs_overlay"] = str(Path(self.tmp) / ".overlay")

    def tearDown(self):
        config._runtime_overrides.pop("monofs_mount", None)
        config._runtime_overrides.pop("monofs_overlay", None)
        config._runtime_overrides.pop("auto_push_enabled", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_workspace_push_requires_confirm_by_default(self):
        config._runtime_overrides["auto_push_enabled"] = False
        result = await execute_tool("workspace_push", {})
        self.assertIn("confirm=true", result)

    async def test_auto_push_waives_confirm_gate(self):
        config._runtime_overrides["auto_push_enabled"] = True
        result = await execute_tool("workspace_push", {})
        # The confirm gate is waived; the handler still reports the mount is inactive.
        self.assertNotIn("confirm=true", result)
        self.assertIn("Error:", result)

    async def test_execute_tool_allowlist_enforced(self):
        result = await execute_tool("guardian_reconcile", {}, allowed={"workspace_read"})
        self.assertIn("not available", result)

    async def test_otto_allowed_all_tools(self):
        # None allowlist = all tools; unknown-persona tool still resolves to its gate error,
        # not an allowlist error.
        result = await execute_tool("guardian_reconcile", {}, allowed=None)
        self.assertNotIn("not available", result)
        self.assertIn("confirm=true", result)


if __name__ == "__main__":
    unittest.main()
