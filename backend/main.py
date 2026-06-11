"""
FastAPI application entry point for the Deep Research Platform.

Starts the API server with CORS, error handlers, structured logging,
and the v1 API router mounted at /api/v1.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import generic_exception_handler, http_exception_handler
from backend.api.v1.router import router as v1_router
from backend.core.config import settings
from backend.core.database import (
    es_connect,
    es_disconnect,
    es_health,
    mongo_connect,
    mongo_disconnect,
    mongo_health,
    postgres_connect,
    postgres_disconnect,
    postgres_health,
)
from backend.schemas import HealthResponse
from backend.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — manage DB connections startup/shutdown."""
    # Startup
    configure_logging(settings.log_level)
    logger.info("application_startup")

    await postgres_connect()
    await mongo_connect()
    await es_connect()

    yield

    # Shutdown
    logger.info("application_shutdown")
    await es_disconnect()
    await mongo_disconnect()
    await postgres_disconnect()


app = FastAPI(
    title="Deep Research Platform",
    description="Academic deep research automation — multi-agent research pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Gzip Compression ─────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=500)

# ── Error Handlers ───────────────────────────────────────────────────
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# ── Routes ───────────────────────────────────────────────────────────
app.include_router(v1_router)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Return health status of all backend services."""
    pg_ok = await postgres_health()
    mongo_ok = await mongo_health()
    es_ok = await es_health()

    all_ok = pg_ok and mongo_ok and es_ok

    return HealthResponse(
        status="healthy" if all_ok else "degraded",
        postgresql=pg_ok,
        mongodb=mongo_ok,
        elasticsearch=es_ok,
    )
