"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.settings import Settings, get_settings

from .routes import router

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
    settings = settings or get_settings()

    if session_factory is None:
        from backend.db.session import get_sessionmaker

        session_factory = get_sessionmaker(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        try:
            from backend.jobs.scheduler import build_scheduler

            scheduler = build_scheduler(settings)
        except Exception:
            # A scheduler that fails to start must not prevent the API from
            # serving already-computed data.
            log.exception("could not start the scheduler; serving read-only")
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(
        title="Sector ETF Breakout & Rotation Dashboard",
        version="0.1.0",
        summary="Absolute and beta-adjusted relative breakout detection across the 11 SPDR sectors.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory

    app.add_middleware(
        CORSMiddleware,
        # The Vite dev server. Tighten this for any real deployment.
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app  # uvicorn factory target: `uvicorn backend.api.app:app --factory`
