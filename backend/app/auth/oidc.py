from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

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
    refresh_token: str = ""
    id_token: str = ""

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


@dataclass
class LoginFlow:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    expires_at: float
    next_path: str = "/"


def _publicize_url(url: str, public_base: str) -> str:
    """Rewrite an in-cluster verification URL to a browser-reachable base."""
    if not url or not public_base:
        return url
    base = urlsplit(public_base.rstrip("/"))
    target = urlsplit(url)
    path = target.path or "/"
    # Preserve the provider path when the base is only an origin.
    if base.path and base.path not in ("", "/") and not path.startswith(base.path):
        path = base.path.rstrip("/") + "/" + path.lstrip("/")
    return urlunsplit((base.scheme, base.netloc, path, target.query, target.fragment))


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


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
        # Machine token: client credentials or device-flow token.
        self._state = TokenState()
        # Interactive browser login (authorization code + PKCE). This takes
        # precedence over the machine token so telemetry is user-scoped.
        self._user_state = TokenState()
        self._device: DeviceFlow | None = None
        self._login: LoginFlow | None = None

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

    @property
    def browser_login_available(self) -> bool:
        return bool(self._setting("agent_oidc_token_url") and self._setting("agent_oidc_client_id"))

    def _authorization_url(self) -> str:
        explicit = self._setting("agent_oidc_authorization_url")
        if explicit:
            return explicit
        public_base = self._setting("agent_oidc_public_base_url")
        if public_base:
            return public_base.rstrip("/") + "/application/o/authorize/"
        issuer = self._setting("agent_oidc_issuer").rstrip("/")
        if not issuer:
            return ""
        # Authentik issuer: https://host/application/o/<app>/
        # Authorize endpoint: https://host/application/o/authorize/
        if "/application/o/" in issuer:
            return issuer.rsplit("/", 1)[0] + "/authorize/"
        return issuer + "/authorize/"

    # -- token access -----------------------------------------------------

    async def get_token(self, *, force: bool = False) -> str:
        static = self._setting("agent_oidc_token")
        if static:
            return static

        async with self._lock:
            # A signed-in user's token wins over the machine identity.
            if self._user_state.valid:
                return self._user_state.access_token
            if self._user_state.refresh_token:
                token_url = self._setting("agent_oidc_token_url")
                client_id = self._setting("agent_oidc_client_id")
                client_secret = self._setting("agent_oidc_client_secret")
                try:
                    payload = await self._request_token(
                        token_url,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": self._user_state.refresh_token,
                        },
                        client_id=client_id,
                        client_secret=client_secret,
                    )
                    self._store_user(payload)
                    if self._user_state.valid:
                        return self._user_state.access_token
                except Exception as exc:  # pragma: no cover - network path
                    logger.warning("OIDC user token refresh failed: %s", exc)
                    self._user_state.refresh_token = ""
                    self._user_state.error = str(exc)

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
                "scope": self._setting("agent_oidc_scope") or "openid email profile offline_access",
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
            refresh_token=str(payload.get("refresh_token") or ""),
            id_token=str(payload.get("id_token") or ""),
        )

    def _store_user(self, payload: dict[str, Any]) -> None:
        try:
            ttl = float(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        token = str(payload.get("access_token") or "").strip()
        previous_refresh = self._user_state.refresh_token
        self._user_state = TokenState(
            access_token=token,
            expires_at=time.time() + max(ttl, 0.0),
            source="login",
            subject=_jwt_subject(token),
            refresh_token=str(payload.get("refresh_token") or previous_refresh),
            id_token=str(payload.get("id_token") or ""),
        )

    # -- browser login (authorization code + PKCE) ------------------------

    async def begin_login(self, redirect_uri: str, next_path: str = "/") -> str:
        authorization_url = self._authorization_url()
        client_id = self._setting("agent_oidc_client_id")
        if not authorization_url or not client_id:
            raise OIDCError(
                "authorization endpoint, token endpoint, and client id are required"
            )

        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        self._login = LoginFlow(
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            expires_at=time.time() + 600,
            next_path=next_path if next_path.startswith("/") else "/",
        )
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": self._setting("agent_oidc_scope") or "openid email profile offline_access",
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        audience = self._setting("agent_oidc_audience")
        if audience:
            params["audience"] = audience
        separator = "&" if "?" in authorization_url else "?"
        return authorization_url + separator + urlencode(params)

    async def complete_login(self, code: str, state: str) -> dict[str, Any]:
        flow = self._login
        if flow is None:
            raise OIDCError("no browser login in progress; start a new login")
        if not state or state != flow.state:
            raise OIDCError("login state mismatch; start a new login")
        if time.time() > flow.expires_at:
            self._login = None
            raise OIDCError("login flow expired; start a new login")
        token_url = self._setting("agent_oidc_token_url")
        client_id = self._setting("agent_oidc_client_id")
        client_secret = self._setting("agent_oidc_client_secret")
        if not token_url or not client_id:
            raise OIDCError("token endpoint and client id are required")
        payload = await self._request_token(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": flow.redirect_uri,
                "code_verifier": flow.code_verifier,
            },
            client_id=client_id,
            client_secret=client_secret,
        )
        self._login = None
        self._store_user(payload)
        return {
            "status": "authorized",
            "subject": self._user_state.subject,
            "expires_at": self._user_state.expires_at,
            "next_path": flow.next_path,
        }

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
            "scope": self._setting("agent_oidc_scope") or "openid email profile offline_access",
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

        # Authentik builds verification URLs from the request Host header. The
        # backend reaches it over the in-cluster service, so those URLs are not
        # usable in a browser. Rewrite them to the configured public origin.
        public_base = self._setting("agent_oidc_public_base_url")
        if public_base:
            for key in ("verification_uri", "verification_uri_complete"):
                payload[key] = _publicize_url(str(payload.get(key) or ""), public_base)

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
        state = self._user_state if self._user_state.access_token else self._state
        return {
            "configured": self.configured,
            "authenticated": bool(token),
            "authenticated_as_user": bool(self._user_state.access_token),
            "source": state.source
            or ("static" if self._setting("agent_oidc_token") else ""),
            "subject": state.subject,
            "expires_at": state.expires_at or None,
            "error": state.error,
            "device_flow_available": self.device_flow_available,
            "browser_login_available": self.browser_login_available,
            "token_url": self._setting("agent_oidc_token_url"),
            "issuer": self._setting("agent_oidc_issuer"),
            "public_base_url": self._setting("agent_oidc_public_base_url"),
        }

    def logout(self) -> None:
        self._state = TokenState()
        self._user_state = TokenState()
        self._device = None
        self._login = None


# Shared provider used by tools, API proxies, and the settings API.
oidc = OIDCTokenProvider()
