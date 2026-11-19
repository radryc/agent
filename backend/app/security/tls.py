"""STRATA TLS environment contract for the agent backend.

This module is the Python counterpart of ``tlsx.LoadEnv``.  It intentionally
has no dependencies on the rest of the application so it can be used by the
uvicorn launcher, configuration validation, and outbound HTTP clients without
creating import cycles.

Environment contract::

    STRATA_TLS_MODE=off|optional|required
    STRATA_TLS_CERT_FILE=/var/run/secrets/strata-tls/tls.crt
    STRATA_TLS_KEY_FILE=/var/run/secrets/strata-tls/tls.key
    STRATA_TLS_CA_FILE=/var/run/secrets/strata-tls/ca.crt
    STRATA_TLS_SERVER_NAME=
    STRATA_TLS_REQUIRE_CLIENT_CERT=true|false

``off`` is the default and preserves the previous plaintext behavior.  In
``required`` mode the certificate, key, and CA files must exist and every
outbound service URL is normalized to HTTPS.  ``optional`` serves TLS when the
certificate/key pair is available and otherwise keeps the plaintext listener,
which makes migration possible without a flag day.

Unlike ``tlsx``'s Go server default, client certificates are *not* required
unless ``STRATA_TLS_REQUIRE_CLIENT_CERT=true``.  The agent's browser-facing
listener is commonly fronted by a proxy that terminates one-way TLS, so the
stricter mTLS mode is an explicit opt-in here.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MODE_OFF = "off"
MODE_OPTIONAL = "optional"
MODE_REQUIRED = "required"

VALID_MODES = frozenset({MODE_OFF, MODE_OPTIONAL, MODE_REQUIRED})

DEFAULT_TLS_DIR = "/var/run/secrets/strata-tls"
DEFAULT_CERT_FILE = f"{DEFAULT_TLS_DIR}/tls.crt"
DEFAULT_KEY_FILE = f"{DEFAULT_TLS_DIR}/tls.key"
DEFAULT_CA_FILE = f"{DEFAULT_TLS_DIR}/ca.crt"

_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", ""})


class TLSConfigError(ValueError):
    """Raised when the STRATA TLS environment contract is invalid."""


@dataclass(frozen=True)
class TLSSettings:
    """Validated view of the STRATA TLS environment contract."""

    mode: str = MODE_OFF
    cert_file: str = DEFAULT_CERT_FILE
    key_file: str = DEFAULT_KEY_FILE
    ca_file: str = DEFAULT_CA_FILE
    server_name: str = ""
    require_client_cert: bool = False
    # ``optional`` mode may keep the listener in plaintext when no certificate
    # is available.  Every other TLS-bearing mode sets this to True.
    serve_tls: bool = False

    @property
    def enabled(self) -> bool:
        """True when the HTTP listener should use TLS."""
        return self.serve_tls

    @property
    def verify_client_cert(self) -> bool:
        """True when uvicorn should request and verify a client certificate."""
        return self.serve_tls and self.require_client_cert

    def client_cert_pair(self) -> tuple[str, str] | None:
        """Return ``(cert, key)`` when both files exist, for outbound mTLS."""
        if _is_file(self.cert_file) and _is_file(self.key_file):
            return self.cert_file, self.key_file
        return None


def _env_value(env: Mapping[str, object], key: str) -> str:
    return str(env.get(key, "") or "").strip()


def _is_file(path: str) -> bool:
    try:
        return Path(path).expanduser().is_file()
    except OSError:
        return False


def _path_value(env: Mapping[str, object], key: str, default: str) -> str:
    raw = _env_value(env, key)
    if not raw:
        return default
    return os.path.expanduser(raw)


def _require_file(key: str, path: str) -> None:
    if not _is_file(path):
        raise TLSConfigError(
            f"{key} must point to an existing file in required TLS mode: {path!r}"
        )


def parse_tls_mode(value: str | None) -> str:
    """Parse ``STRATA_TLS_MODE``; an empty value means ``off``."""
    mode = str(value or "").strip().lower()
    if not mode:
        return MODE_OFF
    if mode not in VALID_MODES:
        raise TLSConfigError(
            f"invalid STRATA_TLS_MODE {mode!r} (want off, optional or required)"
        )
    return mode


def parse_tls_bool(value: object, *, name: str, default: bool = False) -> bool:
    """Parse a boolean env value with unambiguous true/false sets."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise TLSConfigError(f"invalid {name}: {value!r} (want true or false)")


def load_tls_settings(env: Mapping[str, object] | None = None) -> TLSSettings:
    """Load and validate the STRATA TLS contract.

    Only ``required`` mode fails when files are missing.  In ``optional`` mode a
    complete certificate/key pair enables TLS; absent default files leave the
    listener in plaintext, while an explicitly configured but unreadable path is
    treated as a configuration error.
    """
    source: Mapping[str, object] = os.environ if env is None else env
    mode = parse_tls_mode(_env_value(source, "STRATA_TLS_MODE"))
    cert_file = _path_value(source, "STRATA_TLS_CERT_FILE", DEFAULT_CERT_FILE)
    key_file = _path_value(source, "STRATA_TLS_KEY_FILE", DEFAULT_KEY_FILE)
    ca_file = _path_value(source, "STRATA_TLS_CA_FILE", DEFAULT_CA_FILE)
    server_name = _env_value(source, "STRATA_TLS_SERVER_NAME")
    require_client_cert = parse_tls_bool(
        source.get("STRATA_TLS_REQUIRE_CLIENT_CERT"),
        name="STRATA_TLS_REQUIRE_CLIENT_CERT",
        default=False,
    )

    if mode == MODE_OFF:
        return TLSSettings(
            mode=mode,
            cert_file=cert_file,
            key_file=key_file,
            ca_file=ca_file,
            server_name=server_name,
            require_client_cert=False,
            serve_tls=False,
        )

    cert_explicit = bool(_env_value(source, "STRATA_TLS_CERT_FILE"))
    key_explicit = bool(_env_value(source, "STRATA_TLS_KEY_FILE"))
    ca_explicit = bool(_env_value(source, "STRATA_TLS_CA_FILE"))
    cert_ok = _is_file(cert_file)
    key_ok = _is_file(key_file)
    ca_ok = _is_file(ca_file)

    if mode == MODE_REQUIRED:
        _require_file("STRATA_TLS_CERT_FILE", cert_file)
        _require_file("STRATA_TLS_KEY_FILE", key_file)
        _require_file("STRATA_TLS_CA_FILE", ca_file)
        serve_tls = True
    else:  # optional
        if cert_ok and key_ok:
            serve_tls = True
        elif not cert_ok and not key_ok and not (cert_explicit or key_explicit):
            # No PKI mounted: keep the listener plaintext during migration.
            serve_tls = False
        else:
            raise TLSConfigError(
                "STRATA_TLS_MODE=optional requires both STRATA_TLS_CERT_FILE and "
                f"STRATA_TLS_KEY_FILE to exist (cert={cert_file!r}, key={key_file!r})"
            )
        if not ca_ok and (ca_explicit or require_client_cert):
            raise TLSConfigError(
                f"STRATA_TLS_CA_FILE must point to an existing file: {ca_file!r}"
            )

    if require_client_cert and not ca_ok:
        raise TLSConfigError(
            "STRATA_TLS_REQUIRE_CLIENT_CERT=true needs a readable "
            f"STRATA_TLS_CA_FILE: {ca_file!r}"
        )

    return TLSSettings(
        mode=mode,
        cert_file=cert_file,
        key_file=key_file,
        ca_file=ca_file,
        server_name=server_name,
        require_client_cert=require_client_cert,
        serve_tls=serve_tls,
    )


def uvicorn_tls_kwargs(
    settings: TLSSettings | None = None,
    *,
    env: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return the uvicorn ``ssl_*`` keyword arguments for the current mode."""
    cfg = settings or load_tls_settings(env)
    if not cfg.serve_tls:
        return {}

    kwargs: dict[str, object] = {
        "ssl_certfile": cfg.cert_file,
        "ssl_keyfile": cfg.key_file,
        "ssl_cert_reqs": ssl.CERT_REQUIRED if cfg.require_client_cert else ssl.CERT_NONE,
    }
    if _is_file(cfg.ca_file):
        kwargs["ssl_ca_certs"] = cfg.ca_file
    return kwargs


# Readability alias: callers usually ask for "the kwargs uvicorn needs".
uvicorn_kwargs = uvicorn_tls_kwargs


def http_verify(
    settings: TLSSettings | None = None,
    *,
    env: Mapping[str, object] | None = None,
) -> bool | str:
    """Return the ``httpx`` ``verify`` value for outbound service calls.

    A configured CA bundle becomes the verification source.  Without one the
    default trust store is kept, which also preserves plaintext behavior when
    TLS is off (the value is unused for ``http://`` URLs).
    """
    cfg = settings or load_tls_settings(env)
    if cfg.ca_file and _is_file(cfg.ca_file):
        return cfg.ca_file
    return True


def httpx_client_kwargs(
    settings: TLSSettings | None = None,
    *,
    env: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Keyword arguments for internal ``httpx`` clients.

    TLS is left untouched in ``off`` mode so existing plaintext deployments are
    unchanged.  When TLS is configured, the CA bundle is used for verification
    and the service certificate is presented for outbound mTLS.
    """
    cfg = settings or load_tls_settings(env)
    if cfg.mode == MODE_OFF:
        return {}

    kwargs: dict[str, object] = {}
    verify = http_verify(cfg)
    if verify is not True:
        kwargs["verify"] = verify
    client_cert = cfg.client_cert_pair()
    if client_cert is not None:
        kwargs["cert"] = client_cert
    return kwargs


def normalize_url(
    url: str,
    settings: TLSSettings | None = None,
    *,
    env: Mapping[str, object] | None = None,
) -> str:
    """Normalize an outbound service URL for the current TLS mode.

    ``required`` mode adds ``https://`` to scheme-less URLs.  Explicitly
    configured URLs are left untouched: ``https://`` is already secure and an
    ``http://`` URL is an intentional operator choice (for example a local
    model server that does not terminate TLS).  ``off`` and ``optional`` keep
    the existing plaintext default for scheme-less URLs.
    """
    raw = str(url or "").strip()
    if not raw:
        return raw

    if settings is not None:
        required = settings.mode == MODE_REQUIRED
    else:
        source: Mapping[str, object] = os.environ if env is None else env
        required = parse_tls_mode(_env_value(source, "STRATA_TLS_MODE")) == MODE_REQUIRED

    if "://" in raw:
        return raw

    return f"{'https' if required else 'http'}://{raw}"


# Explicit name used by some callers/tests.
normalize_url_scheme = normalize_url
