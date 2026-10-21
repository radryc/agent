from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from app.config import settings, get_runtime

logger = logging.getLogger(__name__)

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}


def _profile_config(profile: str) -> dict:
    """Resolve base_url/api_key/model for an LLM profile.

    "local" -> the llama.cpp / Gemma OpenAI-compatible server (LLM_*).
    "steve" -> the external coding engine profile (STEVE_LLM_*), e.g. DeepSeek.
    """
    if profile == "steve":
        return {
            "base_url": str(get_runtime("steve_llm_base_url") or "https://api.deepseek.com").strip()
            or "https://api.deepseek.com",
            "api_key": str(get_runtime("steve_llm_api_key") or "").strip(),
            "model": str(get_runtime("steve_llm_model") or "deepseek-chat").strip() or "deepseek-chat",
        }
    return {
        "base_url": str(get_runtime("llm_base_url") or settings.llm_base_url).strip(),
        "api_key": str(get_runtime("llm_api_key") or "").strip(),
        "model": str(get_runtime("llm_model") or "gemma-4-E4B-it"),
    }


def _auth_headers(profile: str) -> dict[str, str]:
    key = _profile_config(profile)["api_key"]
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _get_client(profile: str = "local") -> httpx.AsyncClient:
    config = _profile_config(profile)
    cache_key = (config["base_url"], config["api_key"])
    client = _clients.get(cache_key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=config["base_url"],
            headers=_auth_headers(profile),
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        )
        _clients[cache_key] = client
    return client


async def close_client() -> None:
    for client in list(_clients.values()):
        if client and not client.is_closed:
            await client.aclose()
    _clients.clear()


async def chat_completion(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = True,
    profile: str = "local",
) -> AsyncIterator[str] | str:
    config = _profile_config(profile)
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    client = _get_client(profile)

    if not stream:
        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    return _stream_tokens(client, payload)


async def _stream_tokens(
    client: httpx.AsyncClient, payload: dict
) -> AsyncIterator[str]:
    async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                return
            try:
                chunk = json.loads(data_str)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


async def check_health() -> bool:
    # Hosted OpenAI-compatible providers (e.g. DeepSeek) expose /v1/models but
    # not /health; the local llama.cpp server exposes /health.
    try:
        client = _get_client()
        for path in ("/health", "/v1/models"):
            try:
                resp = await client.get(path)
            except Exception:
                continue
            if resp.status_code < 500:
                return True
        return False
    except Exception:
        return False


async def list_models() -> list[dict] | None:
    """Query the OpenAI-compatible /v1/models endpoint.
    Returns list of {id, ...} dicts or None on failure.
    """
    try:
        client = _get_client()
        resp = await client.get("/v1/models")
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("data", [])
    except Exception:
        logger.debug("Failed to list LLM models", exc_info=True)
        return None
