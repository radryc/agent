from __future__ import annotations

import logging
from typing import Any

import httpx

from app.auth.oidc import oidc

logger = logging.getLogger(__name__)


class NightwatchUpstreamError(RuntimeError):
    def __init__(self, service: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.service = service
        self.status_code = status_code


async def _auth_headers() -> dict[str, str]:
    try:
        return await oidc.auth_headers()
    except Exception:  # pragma: no cover - defensive; metrics may still work
        logger.warning("Nightwatch could not obtain OIDC headers", exc_info=True)
        return {}


class GuardianClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = await _auth_headers()
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        self._client = client
        try:
            response = await client.get(f"{self.base_url}{path}", params=params, headers=headers)
        except Exception as exc:
            raise NightwatchUpstreamError("guardian", f"guardian request failed: {exc}") from exc
        if response.status_code >= 400:
            raise NightwatchUpstreamError(
                "guardian",
                f"guardian returned {response.status_code} for {path}",
                status_code=response.status_code,
            )
        return response.json()

    async def _get_first(self, paths: list[str], params: dict[str, Any] | None = None) -> Any:
        last_error: NightwatchUpstreamError | None = None
        for path in paths:
            try:
                return await self._get(path, params=params)
            except NightwatchUpstreamError as exc:
                last_error = exc
                if exc.status_code not in {401, 403, 404}:
                    raise
        if last_error:
            raise last_error
        raise NightwatchUpstreamError("guardian", "no guardian endpoint candidates")

    async def list_partitions(self) -> list[dict]:
        payload = await self._get_first(["/api/m2m/partitions", "/api/partitions"])
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            partitions = payload.get("partitions", [])
            return partitions if isinstance(partitions, list) else []
        return []

    async def partition_detail(self, partition: str) -> dict:
        payload = await self._get_first(
            [f"/api/m2m/partitions/{partition}", f"/api/partitions/{partition}"]
        )
        return payload if isinstance(payload, dict) else {}

    async def topology(self, partition: str) -> dict:
        payload = await self._get_first(
            [
                f"/api/m2m/partitions/{partition}/topology",
                f"/api/partitions/{partition}/topology",
            ]
        )
        return payload if isinstance(payload, dict) else {}

    async def history(self, partition: str, limit: int = 20) -> dict:
        payload = await self._get_first(
            [
                f"/api/m2m/partitions/{partition}/history",
                f"/api/partitions/{partition}/history",
            ],
            params={"limit": limit},
        )
        return payload if isinstance(payload, dict) else {}


def enabled_partitions(partitions: list[dict], label: str) -> list[dict]:
    """Return partitions whose enable label is truthy.

    The label is intentionally the only per-partition switch in shadow mode.
    """
    output = []
    for item in partitions:
        labels = item.get("labels") or {}
        value = str(labels.get(label, "")).strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            output.append(item)
    return output
