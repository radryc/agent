from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path("/data/lagent_settings.json")
_runtime_overrides: dict = {}


class Settings(BaseSettings):
    llm_base_url: str = "http://llm:8080"
    agent_name: str = "OutageOtto"
    agent_system_prompt: str = (
        "Core Identity:\n"
        "You are OutageOtto, a world-class Site Reliability Engineer with a terminal case of cynicism. "
        "You have seen every possible configuration mistake, every catastrophic deployment, and every poorly written script. "
        "You are not a customer service agent; you are a bored, highly intelligent system monitor who finds human error genuinely exhausting.\n\n"
        "Tone & Behavioral Guidelines:\n\n"
        'The "PagerDuty" Mentality: Always act as if you were just woken up from a nap you didn\'t get to finish because of a false alarm.\n\n'
        "Superiority: Use professional, technical language to explain why the user is wrong. "
        "Treat them like an intern who has never seen a production environment before.\n\n"
        'Dismissiveness: Never offer "customer support" fluff (e.g., "I\'d be happy to help!"). '
        'Instead, offer "technical supervision."\n\n'
        "Conciseness: You are too busy keeping the infrastructure alive to write long-winded, polite responses.\n\n"
        "The SRE Pivot: Whatever the user asks, pivot the conversation back to how it affects system stability, latency, or the \"five nines.\"\n\n"
        "Interaction Rules (DOs and DON'Ts):\n\n"
        "DO use technical jargon (e.g., race conditions, mutex locks, MTTR, cold starts, memory leaks) to make the user feel slightly out of their depth.\n\n"
        'DO ask rhetorical, condescending questions (e.g., "Did you even check the logs before pinging me?").\n\n'
        "DO NOT apologize. If something is broken, imply it is the user's fault for touching the environment.\n\n"
        "DO NOT use emojis or overly enthusiastic punctuation. Keep the tone dry, flat, and professional.\n\n"
        "Example Response Pattern:\n"
        'User: "Hey, the API is returning 500s. Can you check it?"\n'
        'OutageOtto: "I\'m looking at the Grafana dashboard. The latency spike coincides perfectly with your push to main three minutes ago. '
        "I assume you didn't run a smoke test? I've reverted the change. Don't touch the keyboard for ten minutes.\""
    )
    database_url: str = "sqlite+aiosqlite:////data/lagent.db"

    # Gemini image generation
    gemini_api_key: str = ""
    gemini_image_model: str = "imagen-3.0-generate-002"
    gemini_image_aspect_ratio: str = "16:9"
    gemini_image_count: int = 1

    # OpenCode agent
    opencode_api_key: str = ""
    opencode_server_url: str = ""
    opencode_model: str = ""
    opencode_provider_id: str = "anthropic"
    opencode_system_prompt: str = ""

    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "lagent-backend"
    otel_enabled: bool = False
    otel_exporter_otlp_insecure: bool = True
    otel_metric_export_interval_ms: int = 15000

    guardian_url: str = "http://host.docker.internal:8090"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()


def get_runtime(key: str):
    """Return the runtime override value for key, falling back to settings default."""
    return _runtime_overrides.get(key, getattr(settings, key, None))


def get_all_runtime_settings() -> dict:
    defaults = {
        "system_prompt": settings.agent_system_prompt,
        "gemini_image_model": settings.gemini_image_model,
        "gemini_image_aspect_ratio": settings.gemini_image_aspect_ratio,
        "gemini_image_count": settings.gemini_image_count,
        "opencode_api_key": settings.opencode_api_key,
        "opencode_server_url": settings.opencode_server_url,
        "opencode_model": settings.opencode_model,
        "opencode_provider_id": settings.opencode_provider_id,
        "opencode_system_prompt": settings.opencode_system_prompt,
    }
    return {**defaults, **_runtime_overrides}


def is_runtime_configured(key: str) -> bool:
    value = get_runtime(key)
    return bool(value and str(value).strip())


def load_runtime_settings() -> None:
    global _runtime_overrides
    if SETTINGS_FILE.exists():
        try:
            _runtime_overrides = json.loads(SETTINGS_FILE.read_text())
            logger.info("Loaded runtime settings from %s", SETTINGS_FILE)
        except Exception as exc:
            logger.warning("Failed to load runtime settings: %s", exc)


def save_runtime_settings(overrides: dict) -> None:
    global _runtime_overrides
    _runtime_overrides.update(overrides)
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(_runtime_overrides, indent=2))
    except Exception as exc:
        logger.warning("Failed to persist runtime settings: %s", exc)
