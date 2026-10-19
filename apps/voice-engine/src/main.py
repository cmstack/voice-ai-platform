from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.config import get_settings
from src.logging_config import configure as configure_logging

configure_logging(get_settings().log_level)  # before importing Pipecat-heavy modules, so their logs are filtered

from src import db  # noqa: E402
from src.routers import calls, leads, recordings  # noqa: E402
from src.services import retention, retry_worker  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for problem in settings.check_security():  # raises in production
        logger.warning(f"Insecure configuration (allowed only in development): {problem}")
    await db.init_pool()
    scheduler = AsyncIOScheduler()
    if settings.enable_retry_worker:
        retry_worker.add_job(scheduler)
    retention.schedule(scheduler)  # always on: retention is a data-protection control, not a feature
    scheduler.start()
    logger.info(f"Voice engine up (telephony={settings.telephony_provider})")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await calls.shutdown_webrtc()
        await db.close_pool()


_docs = get_settings().api_docs_enabled
app = FastAPI(
    title="Voice Engine",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(leads.router)
app.include_router(calls.router)
app.include_router(recordings.router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
