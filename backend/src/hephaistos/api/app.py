"""API factory. Run with uvicorn hephaistos.api.app:create_app --factory.

Schema migrations are always explicit; API processes never load inference runtimes.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from structlog.contextvars import bind_contextvars, reset_contextvars

from hephaistos.api.dependencies import current_principal
from hephaistos.api.errors import ResourceError
from hephaistos.api.resources import register_resources
from hephaistos.config import Settings, load_settings
from hephaistos.observability import logging_context
from hephaistos.persistence.database import create_database_engine
from hephaistos.persistence.models import Artifact
from hephaistos.system.dispatch import DispatchService, DispatchState


class DispatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: StrictBool
    reason: str | None = Field(None, max_length=1000)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    errors: list[dict[str, Any]] | None = None


def problem(
    status: int, title: str, detail: str, code: str, *, errors: list[dict[str, Any]] | None = None
) -> JSONResponse:
    body = Problem(title=title, status=status, detail=detail, code=code, errors=errors)
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status, media_type="application/problem+json")


def create_app(
    *, engine: Engine | None = None, settings: Settings | None = None, now: Callable[[], datetime] = utc_now
) -> FastAPI:
    settings = settings or load_settings()
    owns_engine = engine is None
    database = engine if engine is not None else create_database_engine(settings.hephaistos_data_dir)
    dispatch = DispatchService(database, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with logging_context("api"):
            logger = logging.getLogger(__name__)
            try:
                dispatch.initialize(now=now())
                logger.info("api_started")
                yield
            finally:
                logger.info("api_stopped")
                if owns_engine:
                    database.dispose()

    app = FastAPI(
        title="Hephaistos 3D",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.monotonic()
        tokens = bind_contextvars(request_id=secrets.token_hex(16))
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            try:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                fields: dict[str, Any] = dict(
                    method=request.method,
                    route=route,
                    status=response.status_code if response is not None else 500,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                )
                generation_id = getattr(request.state, "generation_id", None)
                identifier = request.path_params.get("identifier")
                if route.startswith("/api/v1/generations/"):
                    generation_id = identifier
                elif route.startswith("/api/v1/artifacts/") and identifier:
                    try:
                        with Session(database) as session:
                            artifact = session.get(Artifact, identifier)
                            if artifact is not None:
                                generation_id = artifact.generation_id
                    except SQLAlchemyError:
                        fields["artifact_id"] = identifier
                if generation_id:
                    fields["generation_id"] = generation_id
                logging.getLogger(__name__).info("http_request", extra=fields)
            finally:
                reset_contextvars(**tokens)

    router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(current_principal)],
        responses={
            status: {
                "description": "Request failed",
                "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
            }
            for status in (404, 409, 422, 503)
        },
    )

    @router.get("/system/dispatch", response_model=DispatchState, tags=["system"])
    def get_dispatch() -> DispatchState:
        return dispatch.get(now=now())

    @router.put(
        "/system/dispatch",
        response_model=DispatchState,
        tags=["system"],
        responses={
            422: {
                "description": "Request validation failed",
                "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
            }
        },
    )
    def put_dispatch(update: DispatchUpdate) -> DispatchState:
        return dispatch.set(paused=update.paused, reason=update.reason, now=now())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(
            422,
            "Unprocessable Content",
            "Request validation failed",
            "VALIDATION_ERROR",
            errors=[dict(loc=list(error["loc"]), msg=error["msg"], type=error["type"]) for error in exc.errors()],
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        response = problem(exc.status_code, "HTTP Error", str(exc.detail), f"HTTP_{exc.status_code}")
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(ResourceError)
    async def resource_error(_request: Request, exc: ResourceError) -> JSONResponse:
        return problem(exc.status, "Resource error", exc.detail, exc.code, errors=exc.errors)

    register_resources(router, database, settings, now)
    app.include_router(router)
    return app
