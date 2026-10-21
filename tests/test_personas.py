import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import personas  # noqa: E402
from app.agent.tools import build_tool_instructions  # noqa: E402


class PersonaRegistryTest(unittest.TestCase):
    def test_personas_exist(self):
        ids = {p.id for p in personas.list_personas()}
        self.assertEqual(ids, {"otto", "steve"})

    def test_otto_has_access_to_all_tools(self):
        self.assertIsNone(personas.allowed_tools("otto"))

    def test_steve_tool_allowlist(self):
        allowed = personas.allowed_tools("steve")
        self.assertIsNotNone(allowed)
        expected = {
            "monofs_search",
            "doctor_logs_search",
            "agent_sso_status",
            "guardian_overview",
            "guardian_asset_describe",
            "guardian_intent_plan",
            "guardian_partition_validate",
            "workspace_status",
            "workspace_list",
            "workspace_read",
            "workspace_write",
            "workspace_diff",
            "workspace_session",
            "workspace_commit",
            "workspace_push",
        }
        self.assertTrue(expected.issubset(allowed), expected - allowed)

    def test_steve_cannot_use_mutating_infra_tools(self):
        allowed = personas.allowed_tools("steve")
        forbidden = {
            "guardian_reconcile",
            "guardian_deploy_partition",
            "remote_vscode_install",
        }
        self.assertFalse(forbidden & allowed, forbidden & allowed)

    def test_tool_instructions_filtered_per_persona(self):
        steve_prompt = build_tool_instructions(personas.allowed_tools("steve"))
        self.assertIn("workspace_write", steve_prompt)
        self.assertIn("workspace_push", steve_prompt)
        self.assertNotIn("guardian_reconcile", steve_prompt)
        self.assertNotIn("guardian_deploy_partition", steve_prompt)

        otto_prompt = build_tool_instructions(None)
        self.assertIn("guardian_reconcile", otto_prompt)

    def test_steve_default_prompt_used_without_override(self):
        config._runtime_overrides.pop("steve_system_prompt", None)
        self.assertEqual(personas.persona_system_prompt("steve"), personas.STEVE_DEFAULT_PROMPT)

    def test_steve_prompt_includes_strata_skill_playbooks(self):
        config._runtime_overrides.pop("steve_system_prompt", None)
        prompt = personas.persona_system_prompt("steve")
        self.assertIn("asset catalog", prompt)
        self.assertIn("partition validate-local", prompt)
        self.assertIn("doctor_metrics_query", prompt)
        self.assertIn("guardian-partition-authoring", prompt)
        self.assertIn("query-agent-telemetry", prompt)

    def test_steve_prompt_override_wins(self):
        config._runtime_overrides["steve_system_prompt"] = "custom steve prompt"
        try:
            self.assertEqual(personas.persona_system_prompt("steve"), "custom steve prompt")
        finally:
            config._runtime_overrides.pop("steve_system_prompt", None)

    def test_otto_prompt_override_wins(self):
        config._runtime_overrides["system_prompt"] = "custom otto prompt"
        try:
            self.assertEqual(personas.persona_system_prompt("otto"), "custom otto prompt")
        finally:
            config._runtime_overrides.pop("system_prompt", None)


if __name__ == "__main__":
    unittest.main()
