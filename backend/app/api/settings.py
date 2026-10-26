from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.personas import get_persona
from app.agent.workspace import workspace_snapshot
from app.config import get_all_runtime_settings, get_runtime, save_runtime_settings

router = APIRouter(tags=["settings"])


class SettingsPayload(BaseModel):
    system_prompt: str | None = None
    llm_model: str | None = None
    gemini_api_key: str | None = None
    gemini_image_model: str | None = None
    gemini_image_aspect_ratio: str | None = None
    gemini_image_count: int | None = None
    agent_oidc_issuer: str | None = None
    agent_oidc_token_url: str | None = None
    agent_oidc_device_authorization_url: str | None = None
    agent_oidc_authorization_url: str | None = None
    agent_oidc_redirect_url: str | None = None
    agent_oidc_client_id: str | None = None
    agent_oidc_client_secret: str | None = None
    agent_oidc_audience: str | None = None
    agent_oidc_scope: str | None = None
    agent_oidc_token: str | None = None
    agent_oidc_public_base_url: str | None = None
    opencode_api_key: str | None = None
    opencode_server_url: str | None = None
    opencode_model: str | None = None
    opencode_provider_id: str | None = None
    opencode_system_prompt: str | None = None
    steve_engine: str | None = None
    steve_llm_base_url: str | None = None
    steve_llm_model: str | None = None
    steve_llm_api_key: str | None = None
    steve_system_prompt: str | None = None
    auto_switch_enabled: bool | None = None
    auto_push_enabled: bool | None = None
    monofs_router: str | None = None
    monofs_mount: str | None = None
    monofs_overlay: str | None = None


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


@router.get("/api/settings")
async def get_settings():
    data = get_all_runtime_settings()
    gemini_key = (get_runtime("gemini_api_key") or "").strip()
    data["gemini_api_key"] = ""
    data["gemini_api_key_set"] = bool(gemini_key)
    data["gemini_api_key_masked"] = _mask_secret(gemini_key)

    opencode_key = (get_runtime("opencode_api_key") or "").strip()
    data["opencode_api_key"] = ""
    data["opencode_api_key_set"] = bool(opencode_key)
    data["opencode_api_key_masked"] = _mask_secret(opencode_key)

    steve_key = (get_runtime("steve_llm_api_key") or "").strip()
    data["steve_llm_api_key"] = ""
    data["steve_llm_api_key_set"] = bool(steve_key)
    data["steve_llm_api_key_masked"] = _mask_secret(steve_key)

    oidc_secret = (get_runtime("agent_oidc_client_secret") or "").strip()
    data["agent_oidc_client_secret"] = ""
    data["agent_oidc_client_secret_set"] = bool(oidc_secret)
    data["agent_oidc_client_secret_masked"] = _mask_secret(oidc_secret)

    oidc_token = (get_runtime("agent_oidc_token") or "").strip()
    data["agent_oidc_token"] = ""
    data["agent_oidc_token_set"] = bool(oidc_token)
    data["agent_oidc_token_masked"] = _mask_secret(oidc_token)

    # Surface the effective (default) Steve prompt so it can be edited.
    if not str(data.get("steve_system_prompt") or "").strip():
        persona = get_persona("steve")
        if persona is not None:
            data["steve_system_prompt"] = persona.default_prompt

    data["workspace"] = workspace_snapshot()
    return data


@router.put("/api/settings")
async def put_settings(payload: SettingsPayload):
    overrides = {k: v for k, v in payload.model_dump().items() if v is not None}
    for key in (
        "gemini_api_key",
        "opencode_api_key",
        "steve_llm_api_key",
        "agent_oidc_client_secret",
        "agent_oidc_token",
    ):
        if key in overrides and not str(overrides.get(key) or "").strip():
            overrides.pop(key, None)
    save_runtime_settings(overrides)
    return await get_settings()
