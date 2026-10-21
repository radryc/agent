from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Response

from app.config import settings
from app.auth.oidc import oidc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["guardian"])

_TIMEOUT = httpx.Timeout(10.0)


async def _guardian_get_first(paths: list[str]) -> tuple[int, bytes, str]:
    last_status = 502
    last_body = b'{"error":"guardian unreachable"}'
    last_ct = "application/json"
    try:
        headers = await oidc.auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            for raw_path in paths:
                url = f"{settings.guardian_url}/{raw_path.lstrip('/')}"
                r = await client.get(url)
                ct = r.headers.get("content-type", "application/json")
                # Prefer the first non-404 response so we can support both
                # new (/api/m2m/*) and legacy (/api/*) Guardian versions.
                if r.status_code != 404:
                    return r.status_code, r.content, ct
                last_status, last_body, last_ct = r.status_code, r.content, ct
    except Exception as exc:
        logger.warning("guardian proxy error for %s: %s", settings.guardian_url, exc)
        return 502, b'{"error":"guardian unreachable"}', "application/json"
    return last_status, last_body, last_ct


async def _guardian_get(path: str) -> tuple[int, bytes, str]:
    url = f"{settings.guardian_url}/{path.lstrip('/')}"
    try:
        headers = await oidc.auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            r = await client.get(url)
            ct = r.headers.get("content-type", "application/json")
            return r.status_code, r.content, ct
    except Exception as exc:
        logger.warning("guardian proxy error for %s: %s", url, exc)
        return 502, b'{"error":"guardian unreachable"}', "application/json"


@router.get("/api/guardian/overview")
async def guardian_overview():
    status, body, ct = await _guardian_get_first(["/api/m2m/overview", "/api/overview"])
    return Response(content=body, status_code=status, media_type=ct)


@router.get("/api/guardian/partitions")
async def guardian_partitions():
    status, body, ct = await _guardian_get_first(["/api/m2m/partitions", "/api/partitions"])
    return Response(content=body, status_code=status, media_type=ct)


@router.get("/api/guardian/partitions/{name}")
async def guardian_partition_detail(name: str):
    status, body, ct = await _guardian_get_first([f"/api/m2m/partitions/{name}", f"/api/partitions/{name}"])
    return Response(content=body, status_code=status, media_type=ct)
