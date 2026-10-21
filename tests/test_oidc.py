import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.agent import tools as agent_tools  # noqa: E402
from app.auth.oidc import oidc  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def fake_client(*responses: FakeResponse):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post = AsyncMock(side_effect=list(responses))
    return client


class OIDCProviderTest(unittest.IsolatedAsyncioTestCase):
    KEYS = (
        "agent_oidc_issuer",
        "agent_oidc_token_url",
        "agent_oidc_device_authorization_url",
        "agent_oidc_client_id",
        "agent_oidc_client_secret",
        "agent_oidc_audience",
        "agent_oidc_scope",
        "agent_oidc_token",
    )

    def setUp(self):
        oidc.logout()
        for key in self.KEYS:
            config._runtime_overrides.pop(key, None)
        self.addCleanup(oidc.logout)

    def _set(self, **values):
        config._runtime_overrides.update(values)

    async def test_static_token_takes_precedence(self):
        self._set(agent_oidc_token="static-token")
        self.assertEqual(await oidc.get_token(), "static-token")
        self.assertEqual(
            await oidc.auth_headers(),
            {"Authorization": "Bearer static-token"},
        )

    async def test_client_credentials_token_is_cached(self):
        self._set(
            agent_oidc_token_url="http://idp/token",
            agent_oidc_client_id="agent",
            agent_oidc_client_secret="secret",
            agent_oidc_audience="monofs",
        )
        client = fake_client(
            FakeResponse(payload={"access_token": "client-token", "expires_in": 3600})
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            self.assertEqual(await oidc.get_token(), "client-token")
            self.assertEqual(await oidc.get_token(), "client-token")

        self.assertEqual(client.post.await_count, 1)
        _, kwargs = client.post.await_args
        self.assertEqual(kwargs["data"]["grant_type"], "client_credentials")
        self.assertEqual(kwargs["data"]["audience"], "monofs")
        self.assertEqual(kwargs["auth"], ("agent", "secret"))

    async def test_client_credentials_falls_back_to_form_auth(self):
        self._set(
            agent_oidc_token_url="http://idp/token",
            agent_oidc_client_id="agent",
            agent_oidc_client_secret="secret",
        )
        client = fake_client(
            FakeResponse(status_code=401, payload={"error": "invalid_client"}),
            FakeResponse(payload={"access_token": "body-auth-token", "expires_in": 60}),
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            self.assertEqual(await oidc.get_token(), "body-auth-token")

        _, second = client.post.await_args
        self.assertEqual(second["data"]["client_id"], "agent")
        self.assertEqual(second["data"]["client_secret"], "secret")

    async def test_device_flow_stores_token(self):
        self._set(
            agent_oidc_token_url="http://idp/token",
            agent_oidc_device_authorization_url="http://idp/device",
            agent_oidc_client_id="agent",
            agent_oidc_client_secret="secret",
        )
        client = fake_client(
            FakeResponse(
                payload={
                    "device_code": "device-code",
                    "user_code": "123456",
                    "verification_uri": "http://idp/device",
                    "expires_in": 60,
                    "interval": 5,
                }
            ),
            FakeResponse(payload={"access_token": "device-token", "expires_in": 3600}),
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            started = await oidc.start_device_flow()
            self.assertEqual(started["user_code"], "123456")
            polled = await oidc.poll_device_flow()
            self.assertEqual(polled["status"], "authorized")
            self.assertEqual(await oidc.get_token(), "device-token")

        status = await oidc.status()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["source"], "device")

    async def test_device_flow_pending_does_not_store_token(self):
        self._set(
            agent_oidc_token_url="http://idp/token",
            agent_oidc_device_authorization_url="http://idp/device",
            agent_oidc_client_id="agent",
        )
        client = fake_client(
            FakeResponse(
                payload={
                    "device_code": "device-code",
                    "user_code": "123456",
                    "expires_in": 60,
                    "interval": 5,
                }
            ),
            FakeResponse(
                status_code=400,
                payload={"error": "authorization_pending"},
            ),
        )
        with patch("app.auth.oidc.httpx.AsyncClient", return_value=client):
            await oidc.start_device_flow()
            polled = await oidc.poll_device_flow()
            self.assertEqual(polled["status"], "authorization_pending")
            self.assertFalse(await oidc.get_token())

    async def test_doctor_request_attaches_bearer_token(self):
        self._set(agent_oidc_token="static-token")

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.text = "{}"
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response

        with patch("app.agent.tools.httpx.AsyncClient", return_value=client) as client_cls:
            self.assertEqual(
                await agent_tools._doctor_get("/v1/logs/services?tenant=default"),
                "{}",
            )

        _, kwargs = client_cls.call_args
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer static-token",
        )


if __name__ == "__main__":
    unittest.main()
