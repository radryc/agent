from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.session import init_db, close_db
from app.llm.client import close_client
from app.telemetry.otel import init_telemetry, instrument_app
from app.config import load_runtime_settings
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.health import router as health_router
from app.api.settings import router as settings_router
from app.api.guardian import router as guardian_router
from app.api.media import router as media_router
from app.api.agent import router as agent_router
from app.terminal.exec import get_terminal_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting lagent backend...")
    init_telemetry()
    instrument_app(app)
    load_runtime_settings()
    await init_db()
    await get_terminal_manager().start()
    logger.info("lagent backend ready")
    yield
    logger.info("Shutting down lagent backend...")
    await get_terminal_manager().stop()
    await close_client()
    await close_db()


app = FastAPI(title="lagent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(health_router)
app.include_router(settings_router)
app.include_router(guardian_router)
app.include_router(media_router)
app.include_router(agent_router)
