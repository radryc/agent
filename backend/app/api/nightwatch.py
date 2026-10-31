from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nightwatch"])

_TIMEOUT = httpx.Timeout(10.0)


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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
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
