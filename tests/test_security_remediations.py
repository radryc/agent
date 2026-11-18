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
from starlette.websockets import WebSocketDisconnect

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import sre_flow  # noqa: E402
from app.agent.guardrails import redact_sensitive_text  # noqa: E402
from app.agent.tools import get_skill  # noqa: E402
from app.api import chat as chat_api  # noqa: E402
from app.api import guardian as guardian_api  # noqa: E402
from app.api import nightwatch as nightwatch_api  # noqa: E402
from app.api import proposal as proposal_api  # noqa: E402
from app.api import sessions as sessions_api  # noqa: E402
from app.api import settings as settings_api  # noqa: E402
from app.api.auth import router as auth_router  # noqa: E402
from app.auth.oidc import OIDCError, TokenState, oidc  # noqa: E402
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


class ApiAndWebSocketAuthTest(unittest.TestCase):
    def setUp(self):
        self._orig_auth_required = config.settings.agent_auth_required
        self._orig_api_token = config.settings.agent_api_token
        self._orig_terminal_enabled = config.settings.agent_terminal_enabled
        config.settings.agent_auth_required = True
        config.settings.agent_api_token = "test-secret-token"
        config.settings.agent_terminal_enabled = False
        oidc.logout()

        app = FastAPI()
        app.include_router(settings_api.router)
        app.include_router(sessions_api.router)
        app.include_router(guardian_api.router)
        app.include_router(nightwatch_api.router)
        app.include_router(proposal_api.router)
        app.include_router(chat_api.router)
        self.client = TestClient(app)

    def tearDown(self):
        config.settings.agent_auth_required = self._orig_auth_required
        config.settings.agent_api_token = self._orig_api_token
        config.settings.agent_terminal_enabled = self._orig_terminal_enabled
        oidc.logout()

    def test_security_defaults(self):
        defaults = config.Settings()
        self.assertTrue(defaults.agent_auth_required)
        self.assertEqual(defaults.agent_api_token, "")
        self.assertFalse(defaults.agent_terminal_enabled)

    def test_protected_endpoints_return_401_without_auth(self):
        endpoints = [
            ("GET", "/api/settings"),
            ("PUT", "/api/settings"),
            ("GET", "/api/sessions"),
            ("POST", "/api/sessions"),
            ("GET", "/api/sessions/s1"),
            ("DELETE", "/api/sessions/s1"),
            ("PATCH", "/api/sessions/s1"),
            ("GET", "/api/guardian/overview"),
            ("GET", "/api/guardian/partitions"),
            ("GET", "/api/guardian/partitions/demo"),
            ("GET", "/api/nightwatch/overview"),
            ("GET", "/api/nightwatch/brief"),
            ("GET", "/api/nightwatch/assignments"),
            ("GET", "/api/nightwatch/incidents"),
            ("GET", "/api/nightwatch/incidents/inc-1"),
            ("GET", "/api/nightwatch/activity"),
            ("POST", "/api/agent/proposal"),
        ]
        for method, path in endpoints:
            resp = self.client.request(method, path)
            self.assertEqual(resp.status_code, 401, f"{method} {path} should return 401")

    def test_wrong_or_unconfigured_bearer_token_returns_401(self):
        resp = self.client.get(
            "/api/settings",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(resp.status_code, 401)

        config.settings.agent_api_token = ""
        resp_empty = self.client.get(
            "/api/settings",
            headers={"Authorization": "Bearer "},
        )
        self.assertEqual(resp_empty.status_code, 401)

    def test_valid_bearer_token_allows_protected_endpoints(self):
        headers = {"Authorization": "Bearer test-secret-token"}
        with patch.object(
            sessions_api.session_mgr,
            "list_sessions",
            new=AsyncMock(return_value=[]),
        ):
            resp = self.client.get("/api/sessions", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

        settings_resp = self.client.get("/api/settings", headers=headers)
        self.assertEqual(settings_resp.status_code, 200)

    def test_oidc_user_session_allows_access_while_m2m_token_does_not(self):
        # M2M client-credentials token alone must not authenticate user APIs.
        oidc._state = TokenState(
            access_token="m2m-token",
            expires_at=time.time() + 300,
            source="client_credentials",
        )
        self.assertEqual(self.client.get("/api/settings").status_code, 401)

        # Interactive user OIDC session grants access.
        oidc._user_state = TokenState(
            access_token="user-token",
            expires_at=time.time() + 300,
            source="authorization_code",
            subject="alice@example.com",
        )
        self.assertEqual(self.client.get("/api/settings").status_code, 200)

    def test_ws_chat_rejects_unauthenticated_with_1008(self):
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/ws/chat"):
                pass
        self.assertEqual(ctx.exception.code, 1008)

        # Cookie without a valid OIDC user session is also rejected.
        with self.assertRaises(WebSocketDisconnect) as ctx_cookie:
            with self.client.websocket_connect(
                "/ws/chat",
                headers={"Cookie": "lagent_sso=1"},
            ):
                pass
        self.assertEqual(ctx_cookie.exception.code, 1008)

    def test_ws_chat_accepts_query_bearer_and_oidc_cookie(self):
        with self.client.websocket_connect("/ws/chat?token=test-secret-token") as ws:
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

        with self.client.websocket_connect(
            "/ws/chat",
            headers={"Authorization": "Bearer test-secret-token"},
        ) as ws:
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

        oidc._user_state = TokenState(
            access_token="user-token",
            expires_at=time.time() + 300,
            source="authorization_code",
        )
        with self.client.websocket_connect(
            "/ws/chat",
            headers={"Cookie": "lagent_sso=1"},
        ) as ws:
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_ws_chat_blocks_terminal_start_and_exec_when_disabled(self):
        config.settings.agent_terminal_enabled = False
        with patch.object(
            chat_api, "ensure_session", new=AsyncMock(return_value="s1")
        ) as mock_ensure, patch.object(
            chat_api, "execute_command"
        ) as mock_exec:
            with self.client.websocket_connect("/ws/chat?token=test-secret-token") as ws:
                ws.send_json({"type": "terminal_start", "session_id": "s1"})
                self.assertEqual(
                    ws.receive_json(),
                    {"type": "terminal_error", "data": "Terminal is disabled"},
                )

                ws.send_json({"type": "exec", "data": "id", "session_id": "s1"})
                self.assertEqual(
                    ws.receive_json(),
                    {"type": "error", "data": "Terminal execution is disabled"},
                )
            mock_ensure.assert_not_called()
            mock_exec.assert_not_called()

    def test_ws_chat_allows_terminal_when_enabled(self):
        config.settings.agent_terminal_enabled = True
        fake_mgr = MagicMock()
        fake_mgr.subscribe = AsyncMock(return_value=chat_api.asyncio.Queue())
        fake_mgr.unsubscribe = AsyncMock()

        async def fake_exec(_cmd, _sid):
            yield {"type": "exec_output", "data": "ok\n"}

        with patch.object(
            chat_api, "get_terminal_manager", return_value=fake_mgr
        ), patch.object(
            chat_api, "ensure_session", new=AsyncMock(return_value="s1")
        ), patch.object(
            chat_api, "execute_command", side_effect=fake_exec
        ):
            with self.client.websocket_connect("/ws/chat?token=test-secret-token") as ws:
                ws.send_json({"type": "terminal_start", "session_id": "s1"})
                self.assertEqual(
                    ws.receive_json(),
                    {"type": "session", "session_id": "s1"},
                )

                ws.send_json({"type": "exec", "data": "echo ok", "session_id": "s1"})
                self.assertEqual(
                    ws.receive_json(),
                    {"type": "session", "session_id": "s1"},
                )
                self.assertEqual(
                    ws.receive_json(),
                    {"type": "exec_output", "data": "ok\n"},
                )


class DockerfileHardeningTest(unittest.TestCase):
    def test_no_passwordless_sudo_in_dockerfile(self):
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()
        self.assertNotIn("NOPASSWD:ALL", dockerfile)
        self.assertNotIn("/etc/sudoers.d/developer", dockerfile)


if __name__ == "__main__":
    unittest.main()
