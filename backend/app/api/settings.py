from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_all_runtime_settings, get_runtime, save_runtime_settings

router = APIRouter(tags=["settings"])


class SettingsPayload(BaseModel):
    system_prompt: str | None = None
    llm_model: str | None = None
    gemini_api_key: str | None = None
    gemini_image_model: str | None = None
    gemini_image_aspect_ratio: str | None = None
    gemini_image_count: int | None = None
    opencode_api_key: str | None = None
    opencode_server_url: str | None = None
    opencode_model: str | None = None
    opencode_provider_id: str | None = None
    opencode_system_prompt: str | None = None


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

    return data


@router.put("/api/settings")
async def put_settings(payload: SettingsPayload):
    overrides = {k: v for k, v in payload.model_dump().items() if v is not None}
    save_runtime_settings(overrides)
    return await get_settings()
