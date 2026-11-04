"""API factory. Run with uvicorn hephaistos.api.app:create_app --factory.

This first API slice exposes dispatch only; schema migrations are always explicit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from hephaistos.api.dependencies import current_principal
from hephaistos.config import Settings, load_settings
from hephaistos.persistence.database import create_database_engine
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
        try:
            dispatch.initialize(now=now())
            yield
        finally:
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
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(current_principal)])

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

    app.include_router(router)
    return app
