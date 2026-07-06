from __future__ import annotations

from fastapi import APIRouter

from app.agent.opencode import is_opencode_available

router = APIRouter()


@router.get("/api/agent/status")
async def agent_status():
    """Return available agents and their status."""
    opencode_ok = await is_opencode_available()

    return {
        "agents": [
            {
                "id": "local",
                "name": "Local Agent",
                "description": "Gemma 4 via llama.cpp",
                "available": True,
                "default": True,
            },
            {
                "id": "opencode",
                "name": "OpenCode",
                "description": "OpenCode SDK agent via OpenCode server API",
                "available": opencode_ok,
                "default": False,
            },
        ],
        "active": "local",
    }
