from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from app.nightwatch.guardian import NightwatchUpstreamError, _auth_headers

logger = logging.getLogger(__name__)


class DoctorClient:
    """Read-only Doctor query client used by the shadow investigator."""

    def __init__(
        self,
        base_url: str,
        *,
        sentinel_url: str = "",
        tenant: str = "default",
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.sentinel_url = (sentinel_url or base_url).rstrip("/")
        self.tenant = tenant
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self._client

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        headers = await _auth_headers()
        client = await self._client_or_create()
        try:
            response = await client.get(url, params=params, headers=headers)
        except Exception as exc:
            raise NightwatchUpstreamError("doctor", f"doctor request failed: {exc}") from exc
        if response.status_code >= 400:
            raise NightwatchUpstreamError(
                "doctor",
                f"doctor returned {response.status_code} for {response.request.url.path}",
                status_code=response.status_code,
            )
        return response.json()

    # ------------------------------------------------------------------
    # Sentinel alerts
    # ------------------------------------------------------------------

    async def alerts(self, partition: str) -> list[dict]:
        payload = await self._get(
            f"{self.sentinel_url}/v1/alerts",
            params={"tenant": self.tenant, "partition": partition},
        )
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            alerts = payload.get("alerts", [])
            return alerts if isinstance(alerts, list) else []
        return []

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    async def metric(self, query: str, at: datetime | None = None) -> dict:
        params: dict[str, Any] = {"tenant": self.tenant, "query": query}
        if at is not None:
            params["time"] = at.timestamp()
        payload = await self._get(f"{self.base_url}/api/v1/query", params=params)
        return payload if isinstance(payload, dict) else {}

    async def metric_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "60s",
    ) -> dict:
        payload = await self._get(
            f"{self.base_url}/api/v1/query_range",
            params={
                "tenant": self.tenant,
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )
        return payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------------
    # Logs / traces
    # ------------------------------------------------------------------

    async def logs(
        self,
        partition: str,
        *,
        query: str = "error",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 20,
    ) -> dict:
        params: dict[str, Any] = {
            "tenant": self.tenant,
            "partition": partition,
            "q": query,
            "limit": limit,
        }
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        payload = await self._get(f"{self.base_url}/v1/logs/search", params=params)
        return payload if isinstance(payload, dict) else {}

    async def recent_traces(
        self,
        partition: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10,
    ) -> dict:
        params: dict[str, Any] = {
            "tenant": self.tenant,
            "partition": partition,
            "limit": limit,
        }
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        payload = await self._get(f"{self.base_url}/v1/traces/recent", params=params)
        return payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------------
    # Guardian proxies (used if direct Guardian access fails)
    # ------------------------------------------------------------------

    async def guardian_topology(self, partition: str | None = None) -> dict:
        params: dict[str, Any] = {"tenant": self.tenant}
        if partition:
            params["partition"] = partition
        payload = await self._get(f"{self.base_url}/v1/guardian/topology", params=params)
        return payload if isinstance(payload, dict) else {}

    async def guardian_events(self, partition: str, limit: int = 20) -> dict:
        payload = await self._get(
            f"{self.base_url}/v1/guardian/events",
            params={"tenant": self.tenant, "partition": partition, "limit": limit},
        )
        return payload if isinstance(payload, dict) else {}
