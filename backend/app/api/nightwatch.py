from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request

from app.auth.api_auth import require_api_auth
from app.auth.oidc import oidc
from app.config import settings
from app.security.tls import httpx_client_kwargs

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nightwatch"], dependencies=[Depends(require_api_auth)])

_TIMEOUT = httpx.Timeout(10.0)


async def _scope_headers() -> dict[str, str]:
    """Return the partition scope for the signed-in user.

    Doctor owns the access policy. When the browser session is a real user we
    ask Doctor for that user's accessible partitions and forward only the list
    to Nightwatch; Nightwatch never needs the raw OIDC token.
    """
    try:
        status = await oidc.status()
    except Exception:
        return {}
    if not status.get("authenticated_as_user"):
        return {}
    headers = await oidc.auth_headers()
    if not headers:
        return {}
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=headers, **httpx_client_kwargs()
        ) as client:
            response = await client.get(
                f"{settings.doctor_query_url.rstrip('/')}/v1/ui/config"
            )
        if response.status_code != 200:
            return {}
        parts = response.json().get("accessible_partitions")
        if isinstance(parts, list):
            value = ",".join(str(p) for p in parts if str(p).strip())
            return {"X-Nightwatch-Partition-Access": value or "__none__"}
    except Exception as exc:
        logger.warning("Nightwatch partition scope lookup failed: %s", exc)
    return {}


def _empty(path: str) -> dict[str, Any]:
    if path == "overview":
        return {
            "available": False,
            "mode": "shadow",
            "enabled": False,
            "assignments": 0,
            "open_incidents": 0,
            "investigating": 0,
            "handed_off": 0,
            "escalated": 0,
            "resolved": 0,
            "last_poll_at": None,
            "last_error": "Nightwatch is not configured",
        }
    if path == "brief":
        return {
            "available": False,
            "generated_at": None,
            "since": None,
            "counts": {
                "incidents": 0,
                "resolved": 0,
                "handed_off": 0,
                "escalated": 0,
                "failed": 0,
                "recommendations": 0,
            },
            "partitions": [],
            "incidents": [],
            "notable_events": [],
        }
    if path == "assignments":
        return {"available": False, "assignments": []}
    if path == "incidents":
        return {"available": False, "incidents": []}
    if path == "activity":
        return {"available": False, "activity": []}
    return {"available": False}


async def _proxy(path: str, request: Request) -> dict[str, Any]:
    if not settings.nightwatch_url:
        return _empty(path)
    url = f"{settings.nightwatch_url.rstrip('/')}/v1/{path}"
    headers: dict[str, str] = {}
    if settings.nightwatch_proxy_token:
        headers["X-Nightwatch-Token"] = settings.nightwatch_proxy_token
    headers.update(await _scope_headers())
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=headers, **httpx_client_kwargs()
        ) as client:
            response = await client.get(url, params=dict(request.query_params))
    except Exception as exc:
        logger.warning("Nightwatch proxy error for %s: %s", url, exc)
        payload = _empty(path)
        payload["last_error"] = f"Nightwatch unavailable: {exc}"
        return payload
    if response.status_code >= 400:
        logger.warning("Nightwatch proxy returned %s for %s", response.status_code, url)
        payload = _empty(path)
        payload["last_error"] = f"Nightwatch returned {response.status_code}"
        return payload
    try:
        payload = response.json()
    except ValueError:
        payload = _empty(path)
        payload["last_error"] = "Nightwatch returned invalid JSON"
        return payload
    if isinstance(payload, dict):
        payload.setdefault("available", True)
        return payload
    return {"available": True, "data": payload}


@router.get("/api/nightwatch/overview")
async def overview(request: Request):
    return await _proxy("overview", request)


@router.get("/api/nightwatch/brief")
async def brief(request: Request):
    return await _proxy("brief", request)


@router.get("/api/nightwatch/assignments")
async def assignments(request: Request):
    return await _proxy("assignments", request)


@router.get("/api/nightwatch/incidents")
async def incidents(request: Request):
    return await _proxy("incidents", request)


@router.get("/api/nightwatch/incidents/{incident_id}")
async def incident(incident_id: str, request: Request):
    if not settings.nightwatch_url:
        return {"available": False, "error": "Nightwatch is not configured"}
    url = f"{settings.nightwatch_url.rstrip('/')}/v1/incidents/{incident_id}"
    headers: dict[str, str] = {}
    if settings.nightwatch_proxy_token:
        headers["X-Nightwatch-Token"] = settings.nightwatch_proxy_token
    headers.update(await _scope_headers())
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=headers, **httpx_client_kwargs()
        ) as client:
            response = await client.get(url)
    except Exception as exc:
        logger.warning("Nightwatch incident proxy error for %s: %s", url, exc)
        return {"available": False, "error": f"Nightwatch unavailable: {exc}"}
    if response.status_code == 404:
        return {"available": False, "error": "incident not found"}
    if response.status_code >= 400:
        return {"available": False, "error": f"Nightwatch returned {response.status_code}"}
    payload = response.json()
    if isinstance(payload, dict):
        payload.setdefault("available", True)
        return payload
    return {"available": True, "data": payload}


@router.get("/api/nightwatch/activity")
async def activity(request: Request):
    return await _proxy("activity", request)
