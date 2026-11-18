import contextlib
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import personas  # noqa: E402
from app.llm import client  # noqa: E402
from app.llm.grammar import TOOL_CALL_GRAMMAR, looks_like_tool_attempt  # noqa: E402


class ProfileResolutionTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(config._runtime_overrides)

    def tearDown(self):
        config._runtime_overrides.clear()
        config._runtime_overrides.update(self._saved)

    def test_local_profile_defaults(self):
        config._runtime_overrides.pop("llm_base_url", None)
        config._runtime_overrides.pop("llm_model", None)
        profile = client._profile_config("local")
        self.assertEqual(profile["chat_path"], "/v1/chat/completions")
        self.assertEqual(profile["models_path"], "/v1/models")
        self.assertTrue(profile["supports_grammar"])

    def test_fallback_profile_uses_settings(self):
        config._runtime_overrides.update(
            {
                "llm_fallback_base_url": "https://api.example.com",
                "llm_fallback_model": "example-small",
                "llm_fallback_api_key": "test-key",
                "llm_fallback_provider": "",
            }
        )
        profile = client._profile_config("fallback")
        self.assertEqual(profile["base_url"], "https://api.example.com")
        self.assertEqual(profile["model"], "example-small")
        self.assertFalse(profile["supports_grammar"])
        self.assertTrue(client.profile_available("fallback"))

    def test_named_provider_and_path_inference(self):
        config._runtime_overrides["llm_providers_json"] = json.dumps(
            {
                "gemini": {
                    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                    "model": "gemini-2.5-flash",
                    "api_key": "k",
                },
                "bare": "http://host.docker.internal:8080",
            }
        )
        gemini = client._profile_config("provider:gemini")
        self.assertEqual(gemini["chat_path"], "/chat/completions")
        self.assertEqual(gemini["models_path"], "/models")
        self.assertEqual(gemini["model"], "gemini-2.5-flash")

        bare = client._profile_config("provider:bare")
        self.assertEqual(bare["chat_path"], "/v1/chat/completions")
        self.assertFalse(bare["supports_grammar"])

    def test_unknown_provider_raises(self):
        config._runtime_overrides["llm_providers_json"] = "{}"
        with self.assertRaises(ValueError):
            client._profile_config("provider:missing")

    def test_fallback_provider_wins(self):
        config._runtime_overrides.update(
            {
                "llm_providers_json": json.dumps(
                    {"qwen": {"base_url": "https://qwen.example", "model": "qwen3.5-plus", "api_key": "k"}}
                ),
                "llm_fallback_provider": "qwen",
            }
        )
        profile = client._profile_config("fallback")
        self.assertEqual(profile["base_url"], "https://qwen.example")
        self.assertEqual(profile["model"], "qwen3.5-plus")

    def test_engine_profile_mapping(self):
        self.assertEqual(personas.engine_profile("deepseek"), "steve")
        self.assertEqual(personas.engine_profile("provider:gemini"), "provider:gemini")
        self.assertEqual(personas.engine_profile("local"), "local")
        self.assertEqual(personas.engine_profile("opencode"), "local")


@contextlib.contextmanager
def _clean_llm_env(**values):
    keys = (
        "AGENT_LLM_FALLBACK_BASE_URL",
        "AGENT_STEVE_LLM_BASE_URL",
        "LLM_FALLBACK_BASE_URL",
        "STEVE_LLM_BASE_URL",
    )
    saved = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ExternalLLMDefaultsTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(config._runtime_overrides)

    def tearDown(self):
        config._runtime_overrides.clear()
        config._runtime_overrides.update(self._saved)

    def test_external_providers_are_off_by_default(self):
        with _clean_llm_env():
            settings = config.Settings()
        self.assertFalse(settings.llm_hybrid_enabled)
        self.assertEqual(settings.llm_fallback_base_url, "")
        self.assertEqual(settings.steve_llm_base_url, "")
        self.assertFalse(client.profile_available("fallback"))

    def test_agent_prefixed_env_vars_opt_in(self):
        with _clean_llm_env(
            AGENT_LLM_FALLBACK_BASE_URL="https://fallback.example.com",
            AGENT_STEVE_LLM_BASE_URL="https://steve.example.com",
        ):
            settings = config.Settings()
        self.assertEqual(settings.llm_fallback_base_url, "https://fallback.example.com")
        self.assertEqual(settings.steve_llm_base_url, "https://steve.example.com")

    def test_legacy_env_names_do_not_enable_external_llms(self):
        with _clean_llm_env(
            LLM_FALLBACK_BASE_URL="https://fallback.example.com",
            STEVE_LLM_BASE_URL="https://steve.example.com",
        ):
            settings = config.Settings()
        self.assertEqual(settings.llm_fallback_base_url, "")
        self.assertEqual(settings.steve_llm_base_url, "")

    def test_hybrid_runtime_default_is_disabled(self):
        config._runtime_overrides.pop("llm_hybrid_enabled", None)
        self.assertFalse(client.hybrid_enabled())

    def test_deepseek_engine_requires_explicit_base_url(self):
        config._runtime_overrides.update(
            {"steve_llm_base_url": "", "steve_llm_api_key": "test-key"}
        )
        engine = personas._deepseek_engine()
        self.assertFalse(engine.available)
        self.assertIn("AGENT_STEVE_LLM_BASE_URL", engine.detail)


class GrammarTest(unittest.TestCase):
    def test_grammar_requires_tool_key(self):
        self.assertIn("root ::=", TOOL_CALL_GRAMMAR)
        self.assertIn("tool", TOOL_CALL_GRAMMAR)
        self.assertIn("args", TOOL_CALL_GRAMMAR)
        self.assertIn("number ::=", TOOL_CALL_GRAMMAR)

    def test_detects_broken_tool_attempts(self):
        self.assertTrue(looks_like_tool_attempt('{"tool": "doctor_logs_search", "args": {"partition": '))
        self.assertTrue(looks_like_tool_attempt("<|tool_call|>"))
        self.assertFalse(looks_like_tool_attempt("The partition looks healthy."))
        self.assertFalse(looks_like_tool_attempt(""))


if __name__ == "__main__":
    unittest.main()
