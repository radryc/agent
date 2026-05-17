from __future__ import annotations

from fastapi import APIRouter

from app.config import settings, get_runtime
from app.llm.client import check_health as llm_health

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health():
    llm_ok = await llm_health()
    return {
        "status": "ok" if llm_ok else "degraded",
        "llm": "connected" if llm_ok else "unreachable",
        "version": "0.1.0",
    }


@router.get("/api/agent")
async def agent_info():
    return {
        "name": settings.agent_name,
        "system_prompt": get_runtime("system_prompt") or settings.agent_system_prompt,
        "model": "gemma-4-E4B-it-GGUF (Q4_K_M)",
    }
