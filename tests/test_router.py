import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import router  # noqa: E402


class MentionParsingTest(unittest.TestCase):
    def test_extract_mention(self):
        self.assertEqual(router.extract_mention("@steve fix this"), "steve")
        self.assertEqual(router.extract_mention("hey @otto status?"), "otto")
        self.assertEqual(router.extract_mention("@OTTO check this"), "otto")
        self.assertIsNone(router.extract_mention("no mention here"))
        self.assertIsNone(router.extract_mention("email me at bob@steve.com"))

    def test_strip_mention(self):
        self.assertEqual(router.strip_mention("@steve fix this bug"), "fix this bug")
        self.assertEqual(router.strip_mention("ask @otto about it"), "ask about it")
        self.assertEqual(router.strip_mention("no mention"), "no mention")


class KeywordRoutingTest(unittest.TestCase):
    def test_ops_messages_route_to_otto(self):
        self.assertEqual(
            router.keyword_route("the API is returning 500s, check grafana"), "otto"
        )
        self.assertEqual(
            router.keyword_route("the deployment is drifting, reconcile the partition"),
            "otto",
        )

    def test_code_messages_route_to_steve(self):
        self.assertEqual(
            router.keyword_route("write a unit test for this function"), "steve"
        )
        self.assertEqual(
            router.keyword_route("refactor the parser and commit the diff"), "steve"
        )

    def test_ambiguous_messages_route_nowhere(self):
        self.assertIsNone(router.keyword_route("hello there"))
        self.assertIsNone(router.keyword_route("can you look into the logs and the code"))


class FollowupHeuristicTest(unittest.TestCase):
    def test_short_or_affirmative_messages_are_followups(self):
        self.assertTrue(router.looks_like_followup("yes go ahead"))
        self.assertTrue(router.looks_like_followup("do it"))
        self.assertTrue(router.looks_like_followup("ok"))

    def test_substantive_messages_are_not_followups(self):
        self.assertFalse(
            router.looks_like_followup(
                "refactor the parser module and add tests for edge cases"
            )
        )


class ResolvePersonaTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config._runtime_overrides["auto_switch_enabled"] = True

    def tearDown(self):
        config._runtime_overrides.pop("auto_switch_enabled", None)

    async def test_manual_request_wins(self):
        persona, reason = await router.resolve_persona(
            requested="otto", active_persona="steve", message="@steve do it"
        )
        self.assertEqual((persona, reason), ("otto", "manual"))

    async def test_mention_wins_in_auto_mode(self):
        persona, reason = await router.resolve_persona(
            requested="auto", active_persona=None, message="@steve fix the parser"
        )
        self.assertEqual((persona, reason), ("steve", "mention"))

    async def test_keyword_routes_without_llm(self):
        persona, reason = await router.resolve_persona(
            requested="auto",
            active_persona=None,
            message="grafana shows a latency spike after the deployment",
        )
        self.assertEqual((persona, reason), ("otto", "keyword"))

    async def test_sticky_followup_keeps_active_persona(self):
        persona, reason = await router.resolve_persona(
            requested="auto", active_persona="steve", message="yes"
        )
        self.assertEqual((persona, reason), ("steve", "sticky"))

    async def test_auto_switch_disabled_keeps_active(self):
        config._runtime_overrides["auto_switch_enabled"] = False
        persona, reason = await router.resolve_persona(
            requested="auto", active_persona="otto", message="@steve do it"
        )
        self.assertEqual((persona, reason), ("otto", "manual"))

    async def test_auto_switch_disabled_defaults_to_otto(self):
        config._runtime_overrides["auto_switch_enabled"] = False
        persona, reason = await router.resolve_persona(
            requested="auto", active_persona=None, message="hello"
        )
        self.assertEqual((persona, reason), ("otto", "manual"))


if __name__ == "__main__":
    unittest.main()
