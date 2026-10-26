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

    # Steve persona (software engineering agent). Engine auto-detect order is
    # OpenCode -> DeepSeek -> local Gemma unless STEVE_ENGINE forces one.
    steve_engine: str = ""  # "", "auto", "opencode", "deepseek", "local"
    steve_llm_base_url: str = "https://api.deepseek.com"
    steve_llm_model: str = "deepseek-chat"
    steve_llm_api_key: str = ""
    steve_system_prompt: str = ""

    # Persona switching
    auto_switch_enabled: bool = True
    auto_push_enabled: bool = False

    # MonoFS writable workspace (used by Steve to edit/commit/push code)
    monofs_router: str = ""
    monofs_mount: str = "/workspace"
    monofs_overlay: str = "/data/monofs-overlay"

    # Gemini image generation
    gemini_api_key: str = ""
    gemini_image_model: str = "imagen-3.0-generate-002"
    gemini_image_aspect_ratio: str = "16:9"
    gemini_image_count: int = 1

    # OIDC / SSO for Doctor, Guardian, and MonoFS calls.
    # Client credentials are the machine-to-machine path; the device flow is
    # the interactive SSO path (Settings -> SSO -> Sign in).
    agent_oidc_issuer: str = ""
    agent_oidc_token_url: str = ""
    agent_oidc_device_authorization_url: str = ""
    # Browser-facing OIDC endpoints. If empty, derived from the public base.
    agent_oidc_authorization_url: str = ""
    agent_oidc_redirect_url: str = ""
    agent_oidc_client_id: str = ""
    agent_oidc_client_secret: str = ""
    agent_oidc_audience: str = ""
    agent_oidc_scope: str = "openid email profile offline_access"
    agent_oidc_token: str = ""
    # Browser-visible Authentik/IdP base URL. The backend talks to the
    # in-cluster issuer, but the device-flow verification page must be opened
    # by the user's browser (e.g. http://localhost:9020).
    agent_oidc_public_base_url: str = ""

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
        "agent_oidc_issuer": settings.agent_oidc_issuer,
        "agent_oidc_token_url": settings.agent_oidc_token_url,
        "agent_oidc_device_authorization_url": settings.agent_oidc_device_authorization_url,
        "agent_oidc_authorization_url": settings.agent_oidc_authorization_url,
        "agent_oidc_redirect_url": settings.agent_oidc_redirect_url,
        "agent_oidc_client_id": settings.agent_oidc_client_id,
        "agent_oidc_client_secret": settings.agent_oidc_client_secret,
        "agent_oidc_audience": settings.agent_oidc_audience,
        "agent_oidc_scope": settings.agent_oidc_scope,
        "agent_oidc_token": settings.agent_oidc_token,
        "agent_oidc_public_base_url": settings.agent_oidc_public_base_url,
        "opencode_api_key": settings.opencode_api_key,
        "opencode_server_url": settings.opencode_server_url,
        "opencode_model": settings.opencode_model,
        "opencode_provider_id": settings.opencode_provider_id,
        "opencode_system_prompt": settings.opencode_system_prompt,
        "steve_engine": settings.steve_engine,
        "steve_llm_base_url": settings.steve_llm_base_url,
        "steve_llm_model": settings.steve_llm_model,
        "steve_llm_api_key": settings.steve_llm_api_key,
        "steve_system_prompt": settings.steve_system_prompt,
        "auto_switch_enabled": settings.auto_switch_enabled,
        "auto_push_enabled": settings.auto_push_enabled,
        "monofs_router": settings.monofs_router,
        "monofs_mount": settings.monofs_mount,
        "monofs_overlay": settings.monofs_overlay,
    }
    return {**defaults, **_runtime_overrides}


def runtime_bool(key: str, default: bool = False) -> bool:
    """Return a runtime setting coerced to bool."""
    value = get_runtime(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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
