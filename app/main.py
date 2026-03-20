"""
main.py — FastAPI application factory.

Design decisions:
* lifespan context manager (asynccontextmanager) is the modern FastAPI pattern
  for startup/shutdown hooks.  We run `async_engine.begin()` at startup to
  verify the DB connection is healthy before accepting traffic.
* A /health endpoint is included so Docker Compose, Kubernetes liveness probes,
  and the GitHub App setup wizard can verify the server is running.
* CORS is not configured because this server only receives server-to-server
  webhooks from GitHub — a browser never makes requests to it.
* Structured JSON logging is set up here rather than in individual modules so
  the format is consistent across the whole app.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.database.connection import engine
from app.models.database import Base
from app.webhooks.router import router as webhook_router
from app.webhooks.api_router import router as api_router

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run at startup: verify DB connectivity and create tables if needed."""
    logger.info("Starting %s", settings.app_name)
    async with engine.begin() as conn:
        # In production use Alembic migrations; for a fresh dev setup this
        # create_all is a convenience that avoids running alembic manually.
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("SELECT 1"))  # Verify connectivity.
    logger.info("Database connection verified")
    yield
    await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            "GitHub App that analyses pull request diffs for security "
            "vulnerabilities using Claude and posts inline review comments."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
    )

    # Only accept requests from expected hosts in production.
    # In Docker Compose we leave this permissive; tighten for real deployments.
    if not settings.debug:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["*"],  # Tighten to your domain in production.
        )

    app.include_router(webhook_router)
    app.include_router(api_router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "service": settings.app_name}

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "service": settings.app_name,
            "docs": "/docs" if settings.debug else "disabled in production",
        }

    return app


app = create_app()
