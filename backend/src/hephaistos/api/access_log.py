"""Access logging without a body-buffering middleware/task group."""

import logging
import secrets
import time
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, reset_contextvars

from hephaistos.persistence.models import Artifact


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp, *, engine: Engine) -> None:
        self.app, self.engine = app, engine

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started, status = time.monotonic(), 500
        tokens = bind_contextvars(request_id=secrets.token_hex(16))

        async def record_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_status)
        finally:
            try:
                request = Request(scope)
                route = getattr(scope.get("route"), "path", "unmatched")
                fields: dict[str, Any] = dict(
                    method=request.method,
                    route=route,
                    status=status,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                )
                generation_id = getattr(request.state, "generation_id", None)
                identifier = request.path_params.get("identifier")
                if route.startswith("/api/v1/generations/"):
                    generation_id = identifier
                elif route.startswith("/api/v1/artifacts/") and identifier:
                    try:
                        with Session(self.engine) as session:
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
