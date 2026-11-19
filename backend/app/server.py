"""Programmatic uvicorn launcher that applies the STRATA TLS contract.

The Dockerfile used to invoke the ``uvicorn`` CLI directly, which cannot read
the ``STRATA_TLS_*`` environment variables.  Starting through this module keeps
the same app/port defaults while translating the contract into uvicorn's
``ssl_certfile``/``ssl_keyfile``/``ssl_ca_certs``/``ssl_cert_reqs`` options.
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from app.security.tls import (
    MODE_OFF,
    TLSConfigError,
    load_tls_settings,
    uvicorn_tls_kwargs,
)

logger = logging.getLogger(__name__)

DEFAULT_APP = "app.main:app"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8090


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the agent ASGI app with STRATA TLS")
    parser.add_argument("--app", default=DEFAULT_APP, help="ASGI application import path")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port")
    parser.add_argument("--log-level", default="info", help="uvicorn log level")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tls_cfg = load_tls_settings()
    except TLSConfigError as exc:
        logger.error("Invalid STRATA TLS configuration: %s", exc)
        return 2

    tls_kwargs = uvicorn_tls_kwargs(tls_cfg)
    if tls_cfg.serve_tls:
        logger.info(
            "Serving %s on %s:%s with TLS (mode=%s, client_cert=%s)",
            args.app,
            args.host,
            args.port,
            tls_cfg.mode,
            "required" if tls_cfg.require_client_cert else "optional",
        )
    elif tls_cfg.mode == MODE_OFF:
        logger.info("Serving %s on %s:%s (TLS off)", args.app, args.host, args.port)
    else:
        logger.warning(
            "STRATA_TLS_MODE=%s but no certificate/key pair is available; "
            "serving plaintext on %s:%s",
            tls_cfg.mode,
            args.host,
            args.port,
        )

    uvicorn.run(
        args.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        **tls_kwargs,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
