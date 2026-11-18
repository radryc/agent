from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from app.config import get_runtime

# Process-local fallback when no shared secret is configured. Set
# AGENT_SESSION_SECRET (or AGENT_API_TOKEN) to keep cookies valid across
# restarts and multiple backend workers.
_PROCESS_SECRET = secrets.token_urlsafe(32)


def _signing_secret() -> bytes:
    configured = str(get_runtime("agent_session_secret") or "").strip()
    if not configured:
        configured = str(get_runtime("agent_api_token") or "").strip()
    return (configured or _PROCESS_SECRET).encode("utf-8")


def _marker_digest(marker: str) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(
            _signing_secret(),
            f"lagent-sso:{marker}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).rstrip(b"=").decode("ascii")


def sign_session_marker(marker: str) -> str:
    """Return a signed, non-forgeable SSO cookie value for the login marker."""
    if not marker:
        return ""
    return _marker_digest(marker)


def verify_session_marker(cookie_value: str | None, marker: str) -> bool:
    """Return True when the cookie value is a valid signature of the marker."""
    if not cookie_value or not marker:
        return False
    expected = _marker_digest(marker)
    return hmac.compare_digest(str(cookie_value), expected)
