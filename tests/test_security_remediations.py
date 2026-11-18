import base64
import json
import sys
import time
import unittest
from pathlib import Path
import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import sre_flow  # noqa: E402
from app.agent.guardrails import redact_sensitive_text  # noqa: E402
from app.agent.tools import get_skill  # noqa: E402
from app.api.auth import router as auth_router  # noqa: E402
from app.auth.oidc import OIDCError, oidc  # noqa: E402
from app.llm import client as llm_client  # noqa: E402

if importlib.util.find_spec("prometheus_client") is None:
    from starlette.middleware.base import BaseHTTPMiddleware

    class _NoopMetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await call_next(request)

    _otel_stub = MagicMock()
    _metrics_stub = MagicMock(MetricsMiddleware=_NoopMetricsMiddleware)
    with patch.dict(
        sys.modules,
        {
            "app.telemetry.otel": _otel_stub,
            "app.telemetry.metrics": _metrics_stub,
        },
    ):
        from app.main import _cors_settings, app as main_app  # noqa: E402
else:
    from app.main import _cors_settings, app as main_app  # noqa: E402


def _make_jwt(claims: dict, header: dict | None = None) -> str:
    hdr = header or {"alg": "RS256", "typ": "JWT"}
    h_b64 = base64.urlsafe_b64encode(json.dumps(hdr).encode()).rstrip(b"=").decode()
    c_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{h_b64}.{c_b64}.signature"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _fake_http_client(response: FakeResponse):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post = AsyncMock(return_value=response)
    return client


class CorsRemediationTest(unittest.TestCase):
    def test_default_cors_origins_and_credentials(self):
        s = config.Settings()
        expected = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8080",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8080",
        ]
        self.assertEqual(s.cors_allowed_origins, expected)
        origins, allow_creds = _cors_settings(s.cors_allowed_origins)
        self.assertEqual(origins, expected)
        self.assertTrue(allow_creds)

    def test_wildcard_cors_disables_credentials(self):
        origins, allow_creds = _cors_settings(["*"])
        self.assertEqual(origins, ["*"])
        self.assertFalse(allow_creds)

    def test_app_cors_allows_configured_origin_and_blocks_unknown(self):
        client = TestClient(main_app)
        allowed = client.options(
            "/api/auth/status",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(
            allowed.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        self.assertEqual(
            allowed.headers.get("access-control-allow-credentials"),
            "true",
        )

        disallowed = client.options(
            "/api/auth/status",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertIsNone(disallowed.headers.get("access-control-allow-origin"))


class CookieSecureFlagTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(auth_router)
        self.client = TestClient(app)

    def test_callback_sets_secure_cookie_on_forwarded_https(self):
        with patch.object(
            oidc,
            "complete_login",
            AsyncMock(return_value={"next_path": "/"}),
        ):
            resp = self.client.get(
                "/api/auth/callback?code=abc&state=xyz",
                headers={"x-forwarded-proto": "https"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("lagent_sso=1", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)

    def test_callback_omits_secure_cookie_on_plain_http(self):
        with patch.object(
            oidc,
            "complete_login",
            AsyncMock(return_value={"next_path": "/"}),
        ):
            resp = self.client.get(
                "/api/auth/callback?code=abc&state=xyz",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("lagent_sso=1", set_cookie)
        self.assertNotIn("Secure", set_cookie)


class OIDCIdTokenValidationTest(unittest.IsolatedAsyncioTestCase):
    KEYS = (
        "agent_oidc_issuer",
        "agent_oidc_authorization_url",
        "agent_oidc_token_url",
        "agent_oidc_client_id",
        "agent_oidc_client_secret",
    )

    def setUp(self):
        oidc.logout()
        for key in self.KEYS:
            config._runtime_overrides.pop(key, None)
        config._runtime_overrides.update(
            {
                "agent_oidc_issuer": "https://idp.example.com/application/o/lagent/",
                "agent_oidc_authorization_url": "https://idp.example.com/authorize",
                "agent_oidc_token_url": "https://idp.example.com/token",
                "agent_oidc_client_id": "lagent-client",
                "agent_oidc_client_secret": "secret",
            }
        )
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        oidc.logout()
        for key in self.KEYS:
            config._runtime_overrides.pop(key, None)

    async def _start_login(self) -> tuple[str, str]:
        url = await oidc.begin_login("https://app.example.com/api/auth/callback", "/")
        qs = parse_qs(urlsplit(url).query)
        return qs["state"][0], qs["nonce"][0]

    async def test_valid_id_token_succeeds(self):
        state, nonce = await self._start_login()
        id_token = _make_jwt(
            {
                "iss": "https://idp.example.com/application/o/lagent/",
                "aud": "lagent-client",
                "exp": int(time.time()) + 300,
                "nonce": nonce,
                "sub": "alice@example.com",
            }
        )
        client = _fake_http_client(
            FakeResponse(
                payload={
                    "access_token": "acc-1",
                    "expires_in": 3600,
                    "id_token": id_token,
                }
            )
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            res = await oidc.complete_login("code-1", state)
        self.assertEqual(res["status"], "authorized")

    async def test_rejects_mismatched_issuer(self):
        state, nonce = await self._start_login()
        id_token = _make_jwt(
            {
                "iss": "https://attacker.example.com/",
                "aud": "lagent-client",
                "exp": int(time.time()) + 300,
                "nonce": nonce,
            }
        )
        client = _fake_http_client(
            FakeResponse(payload={"access_token": "acc-1", "id_token": id_token})
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            with self.assertRaises(OIDCError) as ctx:
                await oidc.complete_login("code-1", state)
        self.assertIn("issuer", str(ctx.exception))

    async def test_rejects_mismatched_audience(self):
        state, nonce = await self._start_login()
        id_token = _make_jwt(
            {
                "iss": "https://idp.example.com/application/o/lagent/",
                "aud": "other-client",
                "exp": int(time.time()) + 300,
                "nonce": nonce,
            }
        )
        client = _fake_http_client(
            FakeResponse(payload={"access_token": "acc-1", "id_token": id_token})
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            with self.assertRaises(OIDCError) as ctx:
                await oidc.complete_login("code-1", state)
        self.assertIn("audience", str(ctx.exception))

    async def test_rejects_expired_id_token(self):
        state, nonce = await self._start_login()
        id_token = _make_jwt(
            {
                "iss": "https://idp.example.com/application/o/lagent/",
                "aud": "lagent-client",
                "exp": int(time.time()) - 60,
                "nonce": nonce,
            }
        )
        client = _fake_http_client(
            FakeResponse(payload={"access_token": "acc-1", "id_token": id_token})
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            with self.assertRaises(OIDCError) as ctx:
                await oidc.complete_login("code-1", state)
        self.assertIn("expired", str(ctx.exception))

    async def test_rejects_mismatched_nonce(self):
        state, _ = await self._start_login()
        id_token = _make_jwt(
            {
                "iss": "https://idp.example.com/application/o/lagent/",
                "aud": "lagent-client",
                "exp": int(time.time()) + 300,
                "nonce": "wrong-nonce",
            }
        )
        client = _fake_http_client(
            FakeResponse(payload={"access_token": "acc-1", "id_token": id_token})
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            with self.assertRaises(OIDCError) as ctx:
                await oidc.complete_login("code-1", state)
        self.assertIn("nonce", str(ctx.exception))


class ApprovalSessionBindingTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_approval_rejects_mismatched_session_without_consuming(self):
        approval = sre_flow._register_approval(
            "session-owner",
            "guardian_reconcile",
            {"partition": "monofs"},
            rationale="test",
        )
        self.addCleanup(sre_flow._pending_approvals.pop, approval.id, None)

        wrong = await sre_flow.resolve_approval(
            approval.id, True, session_id="session-attacker"
        )
        self.assertFalse(wrong["approved"])
        self.assertIn("does not belong", wrong["text"])
        self.assertIn(approval.id, sre_flow._pending_approvals)

        with patch.object(
            sre_flow, "execute_tool", AsyncMock(return_value="ok")
        ), patch.object(sre_flow.session_mgr, "add_message", AsyncMock()):
            right = await sre_flow.resolve_approval(
                approval.id, True, session_id="session-owner"
            )
        self.assertTrue(right["approved"])
        self.assertNotIn(approval.id, sre_flow._pending_approvals)

    def test_workspace_commit_and_push_remain_mutating(self):
        self.assertFalse(get_skill("workspace_write").mutating)
        self.assertTrue(get_skill("workspace_commit").mutating)
        self.assertTrue(get_skill("workspace_push").mutating)


class ExternalLLMRedactionTest(unittest.IsolatedAsyncioTestCase):
    def test_redact_sensitive_text_patterns(self):
        sample = (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig\n"
            "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
            "OPENAI_KEY=sk-proj-1234567890abcdef123456\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"
            "SSN: 123-45-6789\n"
            "Card: 4111-1111-1111-1111 and 4111111111111111\n"
            "Safe timestamp: 2026-11-18 07:22:03\n"
        )
        redacted = redact_sensitive_text(sample)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", redacted)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted)
        self.assertNotIn("sk-proj-1234567890abcdef123456", redacted)
        self.assertNotIn("MIIEowIBAAKCAQEA", redacted)
        self.assertNotIn("123-45-6789", redacted)
        self.assertNotIn("4111-1111-1111-1111", redacted)
        self.assertNotIn("4111111111111111", redacted)
        self.assertIn("2026-11-18 07:22:03", redacted)

    def test_is_external_llm_url(self):
        self.assertFalse(llm_client.is_external_llm_url("http://llm:8080"))
        self.assertFalse(llm_client.is_external_llm_url("http://localhost:8080"))
        self.assertFalse(llm_client.is_external_llm_url("http://127.0.0.1:11434"))
        self.assertTrue(llm_client.is_external_llm_url("https://api.deepseek.com"))
        self.assertTrue(llm_client.is_external_llm_url("https://api.openai.com/v1"))

    async def test_chat_completion_redacts_only_for_external_urls(self):
        captured: list[dict] = []

        async def fake_post(path, json=None):
            captured.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
            return resp

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=fake_post)

        messages = [{"role": "user", "content": "key AKIAIOSFODNN7EXAMPLE"}]

        with patch.object(
            llm_client,
            "_get_client",
            return_value=(
                mock_http,
                {
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "chat_path": "/v1/chat/completions",
                    "supports_grammar": False,
                    "extra": {},
                },
            ),
        ):
            await llm_client.chat_completion(messages, stream=False, profile="fallback")

        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", captured[0]["messages"][0]["content"])
        # Original caller list is not mutated.
        self.assertIn("AKIAIOSFODNN7EXAMPLE", messages[0]["content"])

        captured.clear()
        with patch.object(
            llm_client,
            "_get_client",
            return_value=(
                mock_http,
                {
                    "base_url": "http://llm:8080",
                    "model": "gemma-4-E4B-it",
                    "chat_path": "/v1/chat/completions",
                    "supports_grammar": True,
                    "extra": {},
                },
            ),
        ):
            await llm_client.chat_completion(messages, stream=False, profile="local")

        self.assertIn("AKIAIOSFODNN7EXAMPLE", captured[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
