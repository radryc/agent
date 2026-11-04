import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import proposal  # noqa: E402


class ProposalApiTest(unittest.TestCase):
    def test_create_proposal_returns_steve_session(self):
        app = FastAPI()
        app.include_router(proposal.router)
        client = TestClient(app)

        async def fake_run(_session_id, _prompt):
            return None

        with patch.object(
            proposal.session_mgr,
            "create_session",
            new=AsyncMock(return_value={"id": "session-steve-1"}),
        ), patch.object(
            proposal, "_mark_handoff", new=AsyncMock()
        ), patch.object(
            proposal, "_run_proposal", new=fake_run
        ):
            response = client.post(
                "/api/agent/proposal",
                json={
                    "incident_id": "inc-1",
                    "incident_title": "GuardianPusherUnhealthy",
                    "partition": "doctor",
                    "severity": "critical",
                    "summary": "pusher heartbeat is stale",
                    "alertname": "GuardianPusherUnhealthy",
                    "labels": {"guardian_pusher": "k8s-main"},
                    "improvement": {
                        "id": "imp-1",
                        "title": "Expose pusher heartbeat",
                        "category": "metrics",
                        "rationale": "missing metric",
                        "proposed_change": "emit guardian_pusher_healthy",
                        "target": "guardian/internal/pusher",
                        "confidence": 0.8,
                    },
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session_id"], "session-steve-1")
        self.assertEqual(response.json()["status"], "started")
