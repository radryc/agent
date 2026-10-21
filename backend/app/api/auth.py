from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.auth.oidc import OIDCError, oidc

router = APIRouter(prefix="/api/auth", tags=["auth"])


class DevicePollPayload(BaseModel):
    device_code: str | None = None


@router.get("/status")
async def auth_status():
    return await oidc.status()


@router.post("/device/start")
async def auth_device_start():
    try:
        return await oidc.start_device_flow()
    except OIDCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/device/poll")
async def auth_device_poll(payload: DevicePollPayload):
    try:
        return await oidc.poll_device_flow(payload.device_code)
    except OIDCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/logout")
async def auth_logout():
    oidc.logout()
    return {"status": "ok"}
