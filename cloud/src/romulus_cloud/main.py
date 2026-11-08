"""FastAPI app factory. Run with `uvicorn --factory romulus_cloud.main:create_app`.

A factory (not a module-level `app`) so importing this module never reads `.env` or opens
an engine; tests build their own app on the test database.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from romulus_cloud import db, errors
from romulus_cloud.api import datasets, device, doctor, ops, proposals, web
from romulus_cloud.api.deps import SessionDep
from romulus_cloud.log import log_line
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import CONTRACT_VERSION


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or db.make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.create_all(engine)
        log_line("api.started", naive_mode=settings.naive_mode, llm_mode=settings.llm_mode)
        yield
        engine.dispose()

    app = FastAPI(title="Romulus cloud", version=CONTRACT_VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = db.make_sessionmaker(engine)
    errors.install(app)

    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        log_line(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return response

    @app.get("/healthz")
    def healthz(session: SessionDep):
        """Liveness plus a real round trip to Postgres."""
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            detail = f"Database check failed ({type(exc).__name__})."
            raise errors.ApiError(503, "db_unavailable", detail) from exc
        return {"status": "ok", "db": "ok", "contract_version": CONTRACT_VERSION}

    app.include_router(device.router)
    app.include_router(doctor.router)  # Fleet Doctor (PR 3 fills it)
    app.include_router(proposals.router)  # Fleet Doctor (PR 4 fills it)
    app.include_router(ops.router)
    app.include_router(datasets.router)
    app.include_router(web.router)
    app.include_router(datasets.pages)
    app.mount("/static", StaticFiles(directory=web.STATIC_DIR), name="static")
    return app
