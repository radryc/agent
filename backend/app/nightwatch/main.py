from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import load_runtime_settings, settings
from app.nightwatch.api import configure, router
from app.nightwatch.doctor import DoctorClient
from app.nightwatch.guardian import GuardianClient
from app.nightwatch.investigator import Investigator
from app.nightwatch.llm import assess
from app.nightwatch.state import state
from app.nightwatch.store import NightwatchStore
from app.nightwatch.worker import NightwatchWorker, worker_loop
from app.telemetry.otel import init_telemetry, instrument_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_runtime_settings()
    requested_mode = str(settings.nightwatch_mode or "shadow").strip().lower()
    if requested_mode != "shadow":
        logger.warning(
            "Nightwatch mode %r is not implemented; forcing shadow mode", requested_mode
        )
    state.mode = "shadow"
    state.enabled = bool(settings.nightwatch_enabled)

    store = NightwatchStore(settings.nightwatch_db_url)
    await store.init()
    configure(store)

    guardian = GuardianClient(settings.guardian_url)
    doctor = DoctorClient(
        settings.doctor_query_url,
        sentinel_url=settings.nightwatch_sentinel_url,
        tenant=settings.nightwatch_tenant,
    )
    investigator = Investigator(guardian, doctor, assessor=assess)
    worker = NightwatchWorker(store, guardian, doctor, investigator)

    task: asyncio.Task | None = None
    if state.enabled:
        task = asyncio.create_task(worker_loop(worker))
        logger.info(
            "Nightwatch ready: mode=%s tenant=%s poll=%ss",
            state.mode,
            settings.nightwatch_tenant,
            settings.nightwatch_poll_interval_seconds,
        )
    else:
        logger.info("Nightwatch disabled by configuration")

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await guardian.close()
        await doctor.close()
        await store.close()


app = FastAPI(title="nightwatch", version="0.1.0", lifespan=lifespan)
init_telemetry("nightwatch")
instrument_app(app)
app.include_router(router)
