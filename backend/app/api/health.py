from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, Response

from app.config import settings, get_runtime
from app.llm.client import check_health as llm_health
from app.telemetry.metrics import LLM_READY, render_metrics

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

_LLM_TTL = float(os.getenv("LAGENT_LLM_HEALTH_TTL_SECONDS", "15"))
_LLM_TIMEOUT = float(os.getenv("LAGENT_LLM_HEALTH_TIMEOUT_SECONDS", "1.0"))
_llm_state = {"ok": False, "checked": 0.0}
_llm_lock = asyncio.Lock()
_refresh_task: asyncio.Task | None = None


async def _refresh_llm_health(force: bool = False) -> bool:
    """Refresh the cached LLM status, coalescing concurrent callers."""
    async with _llm_lock:
        now = time.monotonic()
        if not force and now - _llm_state["checked"] < _LLM_TTL:
            return bool(_llm_state["ok"])
        try:
            ok = await asyncio.wait_for(llm_health(), timeout=_LLM_TIMEOUT)
        except Exception:
            ok = False
        _llm_state["ok"] = bool(ok)
        _llm_state["checked"] = time.monotonic()
        LLM_READY.set(1 if ok else 0)
        return bool(ok)


async def _refresher() -> None:
    while True:
        try:
            await _refresh_llm_health(force=True)
        except Exception:
            logger.exception("LLM health refresh failed")
        await asyncio.sleep(_LLM_TTL)


def start_llm_health_refresher() -> None:
    global _refresh_task
    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.create_task(_refresher())


async def stop_llm_health_refresher() -> None:
    global _refresh_task
    if _refresh_task is not None:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
        _refresh_task = None


@router.get("/api/health")
async def health():
    # Liveness is local and never blocks on a remote provider. The LLM field is
    # the last cached value; /api/ready performs the actual readiness check.
    return {
        "status": "ok",
        "llm": "connected" if _llm_state["ok"] else "unknown",
        "version": "0.1.0",
    }


@router.get("/api/ready")
async def ready(response: Response):
    ok = await _refresh_llm_health()
    response.status_code = 200 if ok else 503
    return {
        "status": "ready" if ok else "degraded",
        "llm": "connected" if ok else "unreachable",
    }


@router.get("/metrics")
async def metrics():
    return render_metrics()


@router.get("/api/agent")
async def agent_info():
    return {
        "name": settings.agent_name,
        "system_prompt": get_runtime("system_prompt") or settings.agent_system_prompt,
        "model": "gemma-4-E4B-it-GGUF (Q4_K_M)",
    }
