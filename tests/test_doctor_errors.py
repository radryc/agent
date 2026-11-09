from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import httpx  # noqa: E402

from app.agent import tools as agent_tools  # noqa: E402


class DoctorErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_doctor_get_surfaces_status_request_id_and_cause(self):
        response = httpx.Response(
            500,
            headers={"X-Request-ID": "req-1"},
            json={
                "status": "error",
                "error": "internal server error",
                "cause": "storage_unavailable",
            },
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response

        with patch.object(agent_tools.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(agent_tools.DoctorQueryError) as ctx:
                await agent_tools._doctor_get("/api/v1/label/name/values")

        message = str(ctx.exception)
        self.assertIn("HTTP 500", message)
        self.assertIn("request_id=req-1", message)
        self.assertIn("cause=storage_unavailable", message)

    async def test_metric_names_tool_returns_attributed_failure(self):
        detail = (
            "HTTP 500 request_id=req-1 cause=storage_unavailable: "
            '{"error":"internal server error","cause":"storage_unavailable"}'
        )
        with patch.object(
            agent_tools,
            "_doctor_get",
            AsyncMock(side_effect=agent_tools.DoctorQueryError(detail)),
        ):
            result = await agent_tools._tool_doctor_metrics_names({})

        self.assertIn("HTTP 500", result)
        self.assertIn("cause=storage_unavailable", result)


if __name__ == "__main__":
    unittest.main()
