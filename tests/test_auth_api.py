import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.config as config  # noqa: E402
from app.api.auth import router as auth_router  # noqa: E402
from app.auth.oidc import oidc  # noqa: E402


class AuthApiTest(unittest.TestCase):
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
        app = FastAPI()
        app.include_router(auth_router)
        self.client = TestClient(app)

    def tearDown(self):
        oidc.logout()
        for key in self.KEYS:
            config._runtime_overrides.pop(key, None)

    def test_status_unconfigured(self):
        response = self.client.get("/api/auth/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["configured"])
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["device_flow_available"])

    def test_device_start_requires_configuration(self):
        response = self.client.post("/api/auth/device/start")
        self.assertEqual(response.status_code, 400)
        self.assertIn("device authorization", response.json()["detail"])

    def test_logout_is_idempotent(self):
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
