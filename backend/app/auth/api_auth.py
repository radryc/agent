from __future__ import annotations

import base64
import json
import secrets

from fastapi import HTTPException, Request, WebSocket, status

from app.auth.oidc import oidc
from app.auth.sso_cookie import verify_session_marker
from app.config import get_runtime, settings


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


def _token_claims(token: str) -> dict:
    """Decode JWT payload claims for local role checks.

    The token was obtained from the configured issuer over the token endpoint;
    this is only used to read group/role claims, never to validate the token.
    """
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _oidc_user_is_admin() -> bool:
    """Return True when the signed-in user has a configured admin group/role."""
    configured = str(get_runtime("agent_admin_groups") or "")
    allowed = {item.strip().lower() for item in configured.split(",") if item.strip()}
    if not allowed:
        return False

    claims = _token_claims(oidc._user_state.access_token)
    groups: set[str] = set()
    for key in ("groups", "roles"):
        value = claims.get(key)
        if isinstance(value, list):
            groups.update(str(item).lower() for item in value)
    realm = claims.get("realm_access")
    if isinstance(realm, dict) and isinstance(realm.get("roles"), list):
        groups.update(str(item).lower() for item in realm["roles"])
    return bool(groups & allowed)


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


async def require_admin_auth(request: Request) -> None:
    """Mutation-grade auth: API token, or an OIDC user in a configured admin group.

    Interactive OIDC users without an admin role must not rewrite security
    settings (issuers, token URLs, client secrets) or LLM credentials.
    """
    if not settings.agent_auth_required:
        return
    if _matches_api_token(_extract_bearer_token(request.headers.get("authorization"))):
        return
    if await _has_valid_oidc_session() and _oidc_user_is_admin():
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator privileges or an API token are required",
    )


async def require_ws_auth(websocket: WebSocket) -> bool:
    """Validate WebSocket auth via ?token=, Bearer header, or lagent_sso OIDC cookie."""
    if not settings.agent_auth_required:
        return True
    if _matches_api_token(websocket.query_params.get("token")):
        return True
    if _matches_api_token(_extract_bearer_token(websocket.headers.get("authorization"))):
        return True
    cookie_value = websocket.cookies.get("lagent_sso")
    if (
        verify_session_marker(cookie_value, oidc.session_marker())
        and await _has_valid_oidc_session()
    ):
        return True
    await websocket.close(code=1008, reason="Unauthorized")
    return False
