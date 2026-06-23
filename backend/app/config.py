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
    agent_name: str = "Lagent"
    agent_system_prompt: str = (
        "You are Lagent, a helpful personal AI assistant. "
        "Be concise, accurate, and friendly. "
        "Think step by step when solving complex problems."
    )
    database_url: str = "sqlite+aiosqlite:////data/lagent.db"

    # Gemini image generation
    gemini_api_key: str = ""
    gemini_image_model: str = "imagen-3.0-generate-002"
    gemini_image_aspect_ratio: str = "16:9"
    gemini_image_count: int = 1

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
