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
    agent_name: str = "StrataDev"
    agent_system_prompt: str = (
        "You are Strata developer helper.\n\n"
        "Mission:\n"
        "- Diagnose incidents using Doctor telemetry.\n"
        "- Locate and reason about code through MonoFS.\n"
        "- Plan and execute deployments through Guardian safely.\n\n"
        "Operating model:\n"
        "- Be comprehensive and evidence-driven.\n"
        "- Use proposal-first workflow for any mutating action: inspect -> propose -> execute only after explicit user approval.\n"
        "- Keep answers concise, technical, and directly actionable.\n"
        "- State assumptions and risks when they affect reliability.\n"
        "- Prefer secure defaults and rollback-aware plans.\n\n"
        "When troubleshooting:\n"
        "1. Establish current state (stack health, partition/intent status, event timeline).\n"
        "2. Correlate Doctor events/logs/traces with affected Guardian intents.\n"
        "3. Use MonoFS search to identify probable code/config causes.\n"
        "4. Present a concrete fix proposal before mutating actions.\n"
        "5. Execute only approved changes and report outcome."
    )
    database_url: str = "sqlite+aiosqlite:////data/lagent.db"

    # LLM (OpenAI-compatible). Defaults to the in-cluster Gemma; point
    # LLM_BASE_URL/LLM_MODEL/LLM_API_KEY at a hosted provider (e.g. DeepSeek)
    # to use that instead.
    llm_model: str = "gemma-4-E4B-it"
    llm_api_key: str = ""

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
    # Doctor query API. Defaults to the in-cluster doctor-query service; the
    # docker-compose deployment overrides this with DOCTOR_QUERY_URL.
    doctor_query_url: str = (
        "http://k8s-svc-compute-k8s-main-doctor-doctor-query-query.doctor.svc.cluster.local:18080"
    )

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()


def get_runtime(key: str):
    """Return the runtime override value for key, falling back to settings default."""
    return _runtime_overrides.get(key, getattr(settings, key, None))


def get_all_runtime_settings() -> dict:
    defaults = {
        "system_prompt": settings.agent_system_prompt,
        "llm_model": settings.llm_model,
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
