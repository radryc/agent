from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from app.config import settings, get_runtime

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _auth_headers() -> dict[str, str]:
    key = str(get_runtime("llm_api_key") or "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.llm_base_url,
            headers=_auth_headers(),
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def chat_completion(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = True,
) -> AsyncIterator[str] | str:
    payload = {
        "model": get_runtime("llm_model") or "gemma-4-E4B-it",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    client = _get_client()

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
