from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src import db
from src.config import get_settings
from src.routers import calls, leads
from src.services import retry_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for problem in settings.check_security():  # raises in production
        logger.warning(f"Insecure configuration (allowed only in development): {problem}")
    await db.init_pool()
    scheduler = retry_worker.start() if settings.enable_retry_worker else None
    logger.info(f"Voice engine up (telephony={settings.telephony_provider})")
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await calls.shutdown_webrtc()
        await db.close_pool()


app = FastAPI(title="Voice Engine", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(leads.router)
app.include_router(calls.router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
