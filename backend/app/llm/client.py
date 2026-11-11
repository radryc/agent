from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from app.config import get_runtime, runtime_bool, settings

logger = logging.getLogger(__name__)

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}

_DEFAULT_CHAT_PATH = "/v1/chat/completions"
_DEFAULT_MODELS_PATH = "/v1/models"


def _parse_json_object(raw: Any, *, what: str) -> dict:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid JSON in %s", what)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring non-object JSON in %s", what)
        return {}
    return parsed


def provider_profiles() -> dict[str, dict]:
    """Named OpenAI-compatible providers from LLM_PROVIDERS_JSON.

    Each entry may be a base URL string or an object:
        {"base_url": str, "model": str, "api_key": str,
         "chat_path": str, "models_path": str, "supports_grammar": bool,
         "extra": {...}}
    """
    raw = _parse_json_object(get_runtime("llm_providers_json"), what="llm_providers_json")
    profiles: dict[str, dict] = {}
    for name, value in raw.items():
        key = str(name).strip()
        if not key:
            continue
        if isinstance(value, str):
            profiles[key] = {"base_url": value}
        elif isinstance(value, dict):
            profiles[key] = dict(value)
    return profiles


def _infer_paths(base_url: str, chat_path: str = "", models_path: str = "") -> tuple[str, str]:
    """Pick OpenAI-compatible paths for well-known base URLs.

    Most providers expose <base>/v1/chat/completions. Google's OpenAI-compatible
    endpoint already includes the version/segment (…/v1beta/openai), so the
    path is just /chat/completions.
    """
    if not chat_path:
        base = base_url.rstrip("/").lower()
        if base.endswith("/openai") or "/openai/" in base:
            chat_path = "/chat/completions"
        else:
            chat_path = _DEFAULT_CHAT_PATH
    if not models_path:
        models_path = "/models" if chat_path == "/chat/completions" else _DEFAULT_MODELS_PATH
    return chat_path, models_path


def _local_config() -> dict:
    base_url = str(get_runtime("llm_base_url") or settings.llm_base_url).strip()
    model = str(get_runtime("llm_model") or settings.llm_model).strip() or settings.llm_model
    extra = _parse_json_object(get_runtime("llm_request_extra"), what="llm_request_extra")
    chat_path, models_path = _infer_paths(base_url)
    return {
        "id": "local",
        "base_url": base_url,
        "api_key": str(get_runtime("llm_api_key") or "").strip(),
        "model": model,
        "chat_path": chat_path,
        "models_path": models_path,
        "supports_grammar": True,
        "extra": extra,
        "label": f"Model ({model})",
    }


def _fallback_config() -> dict:
    provider = str(get_runtime("llm_fallback_provider") or "").strip()
    if provider:
        return _provider_config(provider)

    base_url = str(
        get_runtime("llm_fallback_base_url") or settings.llm_fallback_base_url
    ).strip() or settings.llm_fallback_base_url
    model = str(
        get_runtime("llm_fallback_model") or settings.llm_fallback_model
    ).strip() or settings.llm_fallback_model
    chat_path, models_path = _infer_paths(base_url)
    return {
        "id": "fallback",
        "base_url": base_url,
        "api_key": str(get_runtime("llm_fallback_api_key") or "").strip(),
        "model": model,
        "chat_path": chat_path,
        "models_path": models_path,
        "supports_grammar": False,
        "extra": {},
        "label": f"External API ({model})",
    }


def _provider_config(name: str) -> dict:
    profiles = provider_profiles()
    key = str(name or "").strip()
    entry = profiles.get(key)
    if entry is None:
        # Case-insensitive lookup keeps hand-written LLM_PROVIDERS_JSON friendly.
        for candidate, value in profiles.items():
            if candidate.lower() == key.lower():
                entry = value
                key = candidate
                break
    if entry is None:
        raise ValueError(f"Unknown LLM provider profile: {name!r}")

    base_url = str(entry.get("base_url") or entry.get("url") or "").strip()
    if not base_url:
        raise ValueError(f"LLM provider profile {name!r} has no base_url")

    chat_path, models_path = _infer_paths(
        base_url,
        str(entry.get("chat_path") or "").strip(),
        str(entry.get("models_path") or "").strip(),
    )
    model = str(entry.get("model") or "").strip() or key
    return {
        "id": f"provider:{key}",
        "base_url": base_url,
        "api_key": str(entry.get("api_key") or "").strip(),
        "model": model,
        "chat_path": chat_path,
        "models_path": models_path,
        "supports_grammar": bool(entry.get("supports_grammar", False)),
        "extra": entry.get("extra") if isinstance(entry.get("extra"), dict) else {},
        "label": f"Provider ({model})",
    }


def _profile_config(profile: str) -> dict:
    """Resolve an OpenAI-compatible endpoint profile.

    Profiles:
      - "local":    the llama.cpp / in-cluster server (LLM_*)
      - "steve":    Steve's coding engine (STEVE_LLM_*), e.g. DeepSeek
      - "fallback": the hybrid escalation target (LLM_FALLBACK_*)
      - "provider:<name>": any entry from LLM_PROVIDERS_JSON
    """
    profile = str(profile or "local").strip()
    if profile == "steve":
        base_url = str(
            get_runtime("steve_llm_base_url") or settings.steve_llm_base_url
        ).strip() or settings.steve_llm_base_url
        chat_path, models_path = _infer_paths(base_url)
        return {
            "id": "steve",
            "base_url": base_url,
            "api_key": str(get_runtime("steve_llm_api_key") or "").strip(),
            "model": str(get_runtime("steve_llm_model") or settings.steve_llm_model).strip()
            or settings.steve_llm_model,
            "chat_path": chat_path,
            "models_path": models_path,
            "supports_grammar": False,
            "extra": {},
            "label": "External API",
        }
    if profile == "fallback":
        return _fallback_config()
    if profile.startswith("provider:"):
        return _provider_config(profile.split(":", 1)[1])
    return _local_config()


def profile_supports_grammar(profile: str = "local") -> bool:
    try:
        return bool(_profile_config(profile).get("supports_grammar"))
    except ValueError:
        return False


def profile_available(profile: str = "local") -> bool:
    """Cheap configuration check (does not probe the network)."""
    try:
        config = _profile_config(profile)
    except ValueError:
        return False
    if config["id"] == "local":
        return True
    base_url = str(config.get("base_url") or "")
    if not base_url:
        return False
    if config.get("api_key"):
        return True
    # Self-hosted OpenAI-compatible servers often have no auth.
    local_hosts = ("localhost", "127.0.0.1", "host.docker.internal", "0.0.0.0")
    return any(host in base_url for host in local_hosts)


def profile_label(profile: str = "local") -> str:
    try:
        return str(_profile_config(profile).get("label") or profile)
    except ValueError:
        return profile


def available_profiles() -> list[dict]:
    """Configured profiles, for status/settings surfaces."""
    profiles = [{"id": "local", "available": True, "label": profile_label("local")}]
    try:
        fallback = _fallback_config()
    except ValueError:
        fallback = {
            "label": "Fallback (misconfigured provider)",
            "base_url": "",
            "model": "",
        }
    profiles.append(
        {
            "id": "fallback",
            "available": profile_available("fallback"),
            "label": fallback["label"],
            "base_url": fallback["base_url"],
            "model": fallback["model"],
        }
    )
    for name in provider_profiles():
        try:
            config = _provider_config(name)
        except ValueError:
            continue
        profiles.append(
            {
                "id": config["id"],
                "available": profile_available(config["id"]),
                "label": config["label"],
                "base_url": config["base_url"],
                "model": config["model"],
            }
        )
    return profiles


def _auth_headers(config: dict) -> dict[str, str]:
    key = str(config.get("api_key") or "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _get_client(profile: str = "local") -> tuple[httpx.AsyncClient, dict]:
    config = _profile_config(profile)
    cache_key = (config["base_url"], config["api_key"])
    client = _clients.get(cache_key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=config["base_url"],
            headers=_auth_headers(config),
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0),
        )
        _clients[cache_key] = client
    return client, config


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
    grammar: str | None = None,
) -> AsyncIterator[str] | str:
    client, config = _get_client(profile)
    payload: dict = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    extra = config.get("extra") or {}
    if isinstance(extra, dict):
        for key, value in extra.items():
            # Request-level options win over provider defaults.
            payload.setdefault(key, value)
    if grammar and config.get("supports_grammar"):
        # llama.cpp extension: mask tokens that would violate the GBNF grammar.
        payload["grammar"] = grammar

    if not stream:
        resp = await client.post(config["chat_path"], json=payload)
        resp.raise_for_status()
        data = resp.json()
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = message.get("content")
        if content is None:
            content = message.get("reasoning_content") or ""
        return str(content)

    return _stream_tokens(client, config["chat_path"], payload)


async def _stream_tokens(
    client: httpx.AsyncClient, chat_path: str, payload: dict
) -> AsyncIterator[str]:
    async with client.stream("POST", chat_path, json=payload) as resp:
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
    # Hosted OpenAI-compatible providers expose /v1/models but not /health;
    # the local llama.cpp server exposes /health.
    try:
        client, config = _get_client()
        for path in ("/health", config["models_path"]):
            try:
                resp = await client.get(path)
            except Exception:
                continue
            if resp.status_code < 500:
                return True
        return False
    except Exception:
        return False


async def list_models(profile: str = "local") -> list[dict] | None:
    """Query the OpenAI-compatible models endpoint.

    Returns list of {id, ...} dicts or None on failure.
    """
    try:
        client, config = _get_client(profile)
        resp = await client.get(config["models_path"])
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("data", [])
    except ValueError:
        return None
    except Exception:
        logger.debug("Failed to list LLM models", exc_info=True)
        return None


def hybrid_enabled() -> bool:
    return runtime_bool("llm_hybrid_enabled", default=True)


def fallback_ready() -> bool:
    return hybrid_enabled() and profile_available("fallback")


def hybrid_after_rounds() -> int:
    try:
        return max(1, int(get_runtime("llm_hybrid_after_rounds") or 3))
    except (TypeError, ValueError):
        return 3


def max_tool_rounds() -> int:
    try:
        return max(1, int(get_runtime("llm_max_tool_rounds") or 5))
    except (TypeError, ValueError):
        return 5
