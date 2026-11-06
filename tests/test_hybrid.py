import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import loop  # noqa: E402


class HybridAndGrammarLoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = dict(config._runtime_overrides)

    def tearDown(self):
        config._runtime_overrides.clear()
        config._runtime_overrides.update(self._saved)

    async def test_escalates_to_fallback_after_configured_rounds(self):
        config._runtime_overrides.update(
            {
                "llm_hybrid_enabled": True,
                "llm_hybrid_after_rounds": 1,
                "llm_max_tool_rounds": 3,
                "llm_fallback_provider": "",
                "llm_fallback_base_url": "https://api.example.com",
                "llm_fallback_model": "fallback-model",
                "llm_fallback_api_key": "test-key",
            }
        )
        profiles: list[str] = []

        async def fake_chat(_messages, **kwargs):
            profiles.append(kwargs.get("profile", "local"))
            if len(profiles) == 1:
                return '{"tool": "datetime_now", "args": {}}'
            return "Final answer from the fallback."

        async def fake_exec(_name, _args, allowed=None):
            return "2026-01-01T00:00:00Z"

        with patch.object(loop, "chat_completion", fake_chat), patch.object(
            loop, "execute_tool", fake_exec
        ):
            result = await loop._run_tool_loop(
                [{"role": "user", "content": "what time is it"}]
            )

        self.assertEqual(profiles, ["local", "fallback"])
        self.assertIn("Escalating to", result)
        self.assertIn("Final answer from the fallback.", result)

    async def test_malformed_tool_call_is_retried_with_grammar(self):
        config._runtime_overrides.update(
            {
                "llm_grammar_enabled": True,
                "llm_hybrid_enabled": False,
            }
        )
        grammars: list[str | None] = []

        async def fake_chat(_messages, **kwargs):
            grammars.append(kwargs.get("grammar"))
            if kwargs.get("grammar"):
                return '{"tool": "guardian_overview", "args": {}}'
            if len(grammars) == 1:
                return '{"tool": "guardian_overview", "args": {"partition": '
            return "All partitions are healthy."

        async def fake_exec(_name, _args, allowed=None):
            return '{"partitions": 3}'

        with patch.object(loop, "chat_completion", fake_chat), patch.object(
            loop, "execute_tool", fake_exec
        ):
            result = await loop._run_tool_loop(
                [{"role": "user", "content": "is everything healthy?"}],
                require_tools=True,
            )

        self.assertIsNotNone(grammars[1])
        self.assertIn("tool", grammars[1])
        self.assertIn("All partitions are healthy.", result)

    async def test_grammar_disabled_skips_constrained_retry(self):
        config._runtime_overrides.update(
            {"llm_grammar_enabled": False, "llm_hybrid_enabled": False}
        )
        grammars: list[str | None] = []

        async def fake_chat(_messages, **kwargs):
            grammars.append(kwargs.get("grammar"))
            return '{"tool": "guardian_overview", "args": {"partition": '

        with patch.object(loop, "chat_completion", fake_chat):
            await loop._run_tool_loop(
                [{"role": "user", "content": "is everything healthy?"}],
                require_tools=True,
            )

        self.assertTrue(all(g is None for g in grammars))


if __name__ == "__main__":
    unittest.main()
