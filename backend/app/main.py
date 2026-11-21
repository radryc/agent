from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import audit
from app.agent.session import init_db, close_db
from app.auth.oidc import oidc
from app.llm.client import close_client
from app.telemetry.otel import init_telemetry, instrument_app
from app.telemetry.metrics import MetricsMiddleware
from app.config import load_runtime_settings, settings
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.health import router as health_router, start_llm_health_refresher, stop_llm_health_refresher
from app.api.settings import router as settings_router
from app.api.auth import router as auth_router
from app.api.guardian import router as guardian_router
from app.api.media import router as media_router
from app.api.agent import router as agent_router
from app.api.nightwatch import router as nightwatch_router
from app.api.proposal import router as proposal_router
from app.terminal.exec import get_terminal_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting lagent backend...")
    load_runtime_settings()
    # Authenticate eagerly so Doctor/Guardian/MonoFS calls are ready before the
    # first user turn and /api/auth/status never has to trigger a cold fetch.
    try:
        auth = await oidc.status()
        if auth.get("authenticated"):
            logger.info(
                "OIDC authentication ready (source=%s, expires_at=%s)",
                auth.get("source"),
                auth.get("expires_at"),
            )
        else:
            logger.warning(
                "OIDC authentication unavailable at startup: %s",
                auth.get("error") or "no token configured",
            )
    except Exception:
        logger.exception("OIDC authentication failed during startup")
    await init_db()
    await get_terminal_manager().start()
    start_llm_health_refresher()
    logger.info("lagent backend ready")
    yield
    logger.info("Shutting down lagent backend...")
    await stop_llm_health_refresher()
    await get_terminal_manager().stop()
    await close_client()
    await close_db()


app = FastAPI(title="lagent", version="0.1.0", lifespan=lifespan)

# Instrumentation must be installed before the ASGI application starts;
# Starlette rejects middleware added from inside the lifespan handler.
init_telemetry()
instrument_app(app)

def _cors_settings(origins: list[str] | str | None) -> tuple[list[str], bool]:
    """Return (allowed_origins, allow_credentials); wildcard disables credentials."""
    if isinstance(origins, str):
        parsed = [o.strip() for o in origins.split(",") if o.strip()]
    else:
        parsed = [str(o).strip() for o in (origins or []) if str(o).strip()]
    return parsed, "*" not in parsed


_cors_origins, _cors_allow_credentials = _cors_settings(settings.cors_allowed_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MetricsMiddleware)


@app.middleware("http")
async def audit_requests(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        audit.emit(
            "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write",
            "allow" if response.status_code < 400 else "deny",
            request.url.path,
            scope="agent-http",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            subject=request.headers.get("x-forwarded-user", ""),
            request_id=request.headers.get("x-request-id", ""),
            resource_type="http",
        )
    return response


app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(health_router)
app.include_router(settings_router)
app.include_router(auth_router)
app.include_router(guardian_router)
app.include_router(media_router)
app.include_router(agent_router)
app.include_router(nightwatch_router)
app.include_router(proposal_router)
