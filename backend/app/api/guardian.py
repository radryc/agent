from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Response

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["guardian"])

_TIMEOUT = httpx.Timeout(10.0)


async def _guardian_get(path: str) -> tuple[int, bytes, str]:
    url = f"{settings.guardian_url}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url)
            ct = r.headers.get("content-type", "application/json")
            return r.status_code, r.content, ct
    except Exception as exc:
        logger.warning("guardian proxy error for %s: %s", url, exc)
        return 502, b'{"error":"guardian unreachable"}', "application/json"


@router.get("/api/guardian/overview")
async def guardian_overview():
    status, body, ct = await _guardian_get("/api/overview")
    return Response(content=body, status_code=status, media_type=ct)


@router.get("/api/guardian/partitions")
async def guardian_partitions():
    status, body, ct = await _guardian_get("/api/partitions")
    return Response(content=body, status_code=status, media_type=ct)


@router.get("/api/guardian/partitions/{name}")
async def guardian_partition_detail(name: str):
    status, body, ct = await _guardian_get(f"/api/partitions/{name}")
    return Response(content=body, status_code=status, media_type=ct)
