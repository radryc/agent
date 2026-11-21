from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.auth.oidc import OIDCError, oidc
from app.auth.sso_cookie import sign_session_marker
from app.config import get_runtime

router = APIRouter(prefix="/api/auth", tags=["auth"])


class DevicePollPayload(BaseModel):
    device_code: str | None = None


@router.get("/status")
async def auth_status():
    return await oidc.status()


@router.get("/login")
async def auth_login(request: Request, next: str = "/"):
    """Start the browser authorization-code flow with PKCE."""
    redirect_uri = str(get_runtime("agent_oidc_redirect_url") or "").strip()
    if not redirect_uri:
        redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/callback"
    try:
        url = await oidc.begin_login(redirect_uri, next)
    except OIDCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
async def auth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Finish the browser authorization-code flow and establish the user session."""
    if error:
        detail = error_description or error
        return RedirectResponse(f"/?sso_error={quote(detail)}", status_code=302)
    try:
        result = await oidc.complete_login(code, state)
    except OIDCError as exc:
        return RedirectResponse(f"/?sso_error={quote(str(exc))}", status_code=302)
    response = RedirectResponse(result.get("next_path") or "/", status_code=302)
    # Signed marker bound to the server-side login session. The backend token
    # remains authoritative; the cookie only proves which browser completed SSO.
    forwarded_proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    )
    is_https = request.url.scheme == "https" or forwarded_proto == "https"
    cookie_value = sign_session_marker(oidc.session_marker())
    if cookie_value:
        response.set_cookie(
            "lagent_sso",
            cookie_value,
            httponly=True,
            secure=is_https,
            samesite="lax",
            max_age=8 * 3600,
        )
    return response


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
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("lagent_sso")
    return response
