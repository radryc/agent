from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_all_runtime_settings, save_runtime_settings

router = APIRouter(tags=["settings"])


class SettingsPayload(BaseModel):
    system_prompt: str | None = None
    wan2gp_url: str | None = None
    image_generator: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_seed: int | None = None
    image_loras: str | None = None
    video_generator: str | None = None


@router.get("/api/settings")
async def get_settings():
    return get_all_runtime_settings()


@router.put("/api/settings")
async def put_settings(payload: SettingsPayload):
    overrides = {k: v for k, v in payload.model_dump().items() if v is not None}
    save_runtime_settings(overrides)
    return get_all_runtime_settings()
