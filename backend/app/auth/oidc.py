from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_runtime

logger = logging.getLogger(__name__)

# Refresh a little before the provider expires the token.
TOKEN_REFRESH_SKEW = 60.0
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class OIDCError(Exception):
    """Raised when an OIDC/SSO operation cannot be completed."""


@dataclass
class TokenState:
    access_token: str = ""
    expires_at: float = 0.0
    source: str = ""
    subject: str = ""
    error: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at - TOKEN_REFRESH_SKEW


@dataclass
class DeviceFlow:
    device_code: str
    interval: int
    expires_at: float
    client_id: str
    client_secret: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _jwt_subject(token: str) -> str:
    """Decode the JWT payload for display only; never trust it for authorization."""
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        return str(data.get("email") or data.get("sub") or "")
    except Exception:
        return ""


class OIDCTokenProvider:
    """OIDC token cache for Doctor, Guardian, and MonoFS calls.

    Resolution order:
    1. `agent_oidc_token` static/break-glass token.
    2. A cached token from device-code login.
    3. Client-credentials token using `agent_oidc_client_id` +
       `agent_oidc_client_secret`.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = TokenState()
        self._device: DeviceFlow | None = None

    # -- settings ---------------------------------------------------------

    def _setting(self, key: str) -> str:
        return str(get_runtime(key) or "").strip()

    @property
    def configured(self) -> bool:
        return bool(
            self._setting("agent_oidc_token")
            or (
                self._setting("agent_oidc_token_url")
                and self._setting("agent_oidc_client_id")
            )
        )

    @property
    def device_flow_available(self) -> bool:
        return bool(
            self._setting("agent_oidc_device_authorization_url")
            and self._setting("agent_oidc_token_url")
            and self._setting("agent_oidc_client_id")
        )

    # -- token access -----------------------------------------------------

    async def get_token(self, *, force: bool = False) -> str:
        static = self._setting("agent_oidc_token")
        if static:
            return static

        async with self._lock:
            if not force and self._state.valid:
                return self._state.access_token

            token_url = self._setting("agent_oidc_token_url")
            client_id = self._setting("agent_oidc_client_id")
            client_secret = self._setting("agent_oidc_client_secret")
            if not token_url or not client_id or not client_secret:
                # No machine credentials. A device-flow token may still be cached.
                if self._state.valid:
                    return self._state.access_token
                return ""

            data = {
                "grant_type": "client_credentials",
                "scope": self._setting("agent_oidc_scope") or "openid",
            }
            audience = self._setting("agent_oidc_audience")
            if audience:
                data["audience"] = audience

            try:
                payload = await self._request_token(
                    token_url,
                    data=data,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            except Exception as exc:  # pragma: no cover - network path
                self._state.error = str(exc)
                logger.warning("OIDC client-credentials token request failed: %s", exc)
                return ""

            self._store(payload, source="client_credentials")
            return self._state.access_token

    async def auth_headers(self) -> dict[str, str]:
        token = await self.get_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _request_token(
        self,
        token_url: str,
        *,
        data: dict[str, str],
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            # Most OIDC providers accept HTTP Basic client authentication.
            response = await client.post(
                token_url, data=data, auth=(client_id, client_secret)
            )
            if response.status_code >= 400:
                # Some providers require the client credentials in the body.
                fallback = dict(data)
                fallback["client_id"] = client_id
                fallback["client_secret"] = client_secret
                response = await client.post(token_url, data=fallback)
            if response.status_code >= 400:
                raise OIDCError(
                    f"token endpoint returned {response.status_code}: "
                    f"{response.text[:500]}"
                )
            payload = response.json()
        if not str(payload.get("access_token") or "").strip():
            raise OIDCError("token endpoint returned no access_token")
        return payload

    def _store(self, payload: dict[str, Any], *, source: str) -> None:
        try:
            ttl = float(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        token = str(payload.get("access_token") or "").strip()
        self._state = TokenState(
            access_token=token,
            expires_at=time.time() + max(ttl, 0.0),
            source=source,
            subject=_jwt_subject(token),
        )

    # -- device flow ------------------------------------------------------

    async def start_device_flow(self) -> dict[str, Any]:
        device_url = self._setting("agent_oidc_device_authorization_url")
        client_id = self._setting("agent_oidc_client_id")
        if not device_url or not client_id:
            raise OIDCError(
                "device authorization endpoint and agent_oidc_client_id are required"
            )

        data = {
            "client_id": client_id,
            "scope": self._setting("agent_oidc_scope") or "openid",
        }
        audience = self._setting("agent_oidc_audience")
        if audience:
            data["audience"] = audience

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(device_url, data=data)
        if response.status_code >= 400:
            raise OIDCError(
                f"device authorization endpoint returned {response.status_code}: "
                f"{response.text[:500]}"
            )
        payload = response.json()
        device_code = str(payload.get("device_code") or "").strip()
        if not device_code:
            raise OIDCError("device authorization response has no device_code")

        try:
            interval = max(1, int(payload.get("interval") or 5))
        except (TypeError, ValueError):
            interval = 5
        try:
            expires_in = max(1, int(payload.get("expires_in") or 600))
        except (TypeError, ValueError):
            expires_in = 600

        self._device = DeviceFlow(
            device_code=device_code,
            interval=interval,
            expires_at=time.time() + expires_in,
            client_id=client_id,
            client_secret=self._setting("agent_oidc_client_secret"),
        )
        return {
            "user_code": payload.get("user_code", ""),
            "verification_uri": payload.get("verification_uri", ""),
            "verification_uri_complete": payload.get("verification_uri_complete", ""),
            "expires_in": expires_in,
            "interval": interval,
        }

    async def poll_device_flow(self, device_code: str | None = None) -> dict[str, Any]:
        device = self._device
        if device is None:
            raise OIDCError("no device flow in progress; start a new login")
        if device_code and device_code != device.device_code:
            raise OIDCError("device_code does not match the active login")
        if time.time() > device.expires_at:
            self._device = None
            return {"status": "expired"}

        token_url = self._setting("agent_oidc_token_url")
        if not token_url:
            raise OIDCError("agent_oidc_token_url is required for device login")

        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device.device_code,
            "client_id": device.client_id,
        }
        if device.client_secret:
            data["client_secret"] = device.client_secret

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(token_url, data=data)
        try:
            payload = response.json()
        except Exception:
            payload = {}

        if response.status_code >= 400:
            error = str(payload.get("error") or "").strip()
            if error in {"authorization_pending", "slow_down"}:
                return {"status": error, "interval": device.interval}
            if error == "expired_token":
                self._device = None
                return {"status": "expired"}
            if error == "access_denied":
                self._device = None
                return {"status": "denied"}
            raise OIDCError(
                f"token endpoint returned {response.status_code}: "
                f"{response.text[:500]}"
            )

        self._store(payload, source="device")
        self._device = None
        return {
            "status": "authorized",
            "subject": self._state.subject,
            "expires_at": self._state.expires_at,
        }

    # -- status / logout --------------------------------------------------

    async def status(self) -> dict[str, Any]:
        token = await self.get_token()
        return {
            "configured": self.configured,
            "authenticated": bool(token),
            "source": self._state.source
            or ("static" if self._setting("agent_oidc_token") else ""),
            "subject": self._state.subject,
            "expires_at": self._state.expires_at or None,
            "error": self._state.error,
            "device_flow_available": self.device_flow_available,
            "token_url": self._setting("agent_oidc_token_url"),
            "issuer": self._setting("agent_oidc_issuer"),
        }

    def logout(self) -> None:
        self._state = TokenState()
        self._device = None


# Shared provider used by tools, API proxies, and the settings API.
oidc = OIDCTokenProvider()
