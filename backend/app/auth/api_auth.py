from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, WebSocket, status

from app.auth.oidc import oidc
from app.config import settings


def _extract_bearer_token(auth_header: str | None) -> str:
    if not auth_header:
        return ""
    scheme, _, token = auth_header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _matches_api_token(candidate: str | None) -> bool:
    expected = (settings.agent_api_token or "").strip()
    if not expected or not candidate:
        return False
    return secrets.compare_digest(candidate.strip(), expected)


async def _has_valid_oidc_session() -> bool:
    """Return True when an interactive OIDC user session is active."""
    if oidc._user_state.valid:
        return True
    if oidc._user_state.refresh_token:
        await oidc.get_token()
        return oidc._user_state.valid
    return False


async def require_api_auth(request: Request) -> None:
    """FastAPI dependency enforcing API auth via Bearer token or OIDC user session."""
    if not settings.agent_auth_required:
        return
    if _matches_api_token(_extract_bearer_token(request.headers.get("authorization"))):
        return
    if await _has_valid_oidc_session():
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_ws_auth(websocket: WebSocket) -> bool:
    """Validate WebSocket auth via ?token=, Bearer header, or lagent_sso OIDC cookie."""
    if not settings.agent_auth_required:
        return True
    if _matches_api_token(websocket.query_params.get("token")):
        return True
    if _matches_api_token(_extract_bearer_token(websocket.headers.get("authorization"))):
        return True
    if websocket.cookies.get("lagent_sso") == "1" and await _has_valid_oidc_session():
        return True
    await websocket.close(code=1008, reason="Unauthorized")
    return False
