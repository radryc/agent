from __future__ import annotations

import logging

from fastapi import APIRouter

from app.agent.opencode import is_opencode_available, list_opencode_models, list_opencode_providers
from app.llm.client import list_models as list_llm_models
from app.config import get_runtime

logger = logging.getLogger(__name__)
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


@router.get("/api/agent/models")
async def agent_models():
    """Return discoverable models from local LLM and OpenCode server."""
    result: dict = {
        "local": {
            "provider": "openai-compatible",
            "base_url": str(get_runtime("llm_base_url") or ""),
            "current_model": str(get_runtime("llm_model") or "gemma-4-E4B-it"),
            "models": [],
        },
        "opencode": {
            "provider": str(get_runtime("opencode_provider_id") or "anthropic"),
            "server_url": str(get_runtime("opencode_server_url") or ""),
            "current_model": str(get_runtime("opencode_model") or ""),
            "models": [],
            "providers": [],
        },
        "known_providers": [
            {"id": "anthropic", "name": "Anthropic"},
            {"id": "openai", "name": "OpenAI"},
            {"id": "google-vertex", "name": "Google Vertex AI"},
            {"id": "amazon-bedrock", "name": "Amazon Bedrock"},
            {"id": "openrouter", "name": "OpenRouter"},
            {"id": "groq", "name": "Groq"},
            {"id": "deepseek", "name": "DeepSeek"},
            {"id": "github-copilot", "name": "GitHub Copilot"},
            {"id": "ollama", "name": "Ollama (local)"},
            {"id": "lmstudio", "name": "LM Studio (local)"},
            {"id": "openai-compatible", "name": "OpenAI-compatible API"},
            {"id": "opencode-go", "name": "OpenCode Go"},
        ],
    }

    llm_models = await list_llm_models()
    if llm_models:
        result["local"]["models"] = [
            {"id": m.get("id", ""), "name": m.get("id", "")}
            for m in llm_models
        ]

    opencode_ok = await is_opencode_available()
    if opencode_ok:
        oc_models = await list_opencode_models()
        result["opencode"]["models"] = oc_models
        oc_providers = await list_opencode_providers()
        result["opencode"]["providers"] = oc_providers

    return result
