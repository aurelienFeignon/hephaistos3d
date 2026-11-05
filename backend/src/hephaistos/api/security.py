"""ASGI checks run before FastAPI reads any request body."""

from __future__ import annotations

import asyncio
import hmac
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hephaistos.config import Settings


class BodyRejected(HTTPException):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(status, detail)
        self.code = code


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.hosts = {value.strip().lower().strip("[]") for value in settings.api_allowed_hosts.split(",")}
        self.origins = {value.strip() for value in settings.api_allowed_origins.split(",")}
        secret = settings.api_access_token
        self.authorization = ("Bearer " + (secret.get_secret_value() if secret else "")).encode()

    @staticmethod
    async def reject(scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
        response = JSONResponse(
            dict(
                type="about:blank",
                title="Request rejected",
                status=status,
                detail=code.replace("_", " ").capitalize(),
                code=code,
            ),
            status_code=status,
            media_type="application/problem+json",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                **({"WWW-Authenticate": "Bearer"} if status == 401 else {}),
            },
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        try:
            hosts = headers.getlist("host")
            parsed = urlsplit("//" + hosts[0]) if len(hosts) == 1 else None
            if parsed is None or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("Invalid host")
            _ = parsed.port
            if parsed.hostname is None or parsed.hostname.lower() not in self.hosts:
                raise ValueError("Host not allowed")
        except ValueError:
            await self.reject(scope, receive, send, 400, "HOST_NOT_ALLOWED")
            return
        health = scope["path"] == "/api/v1/system/health" and scope["method"] in {"GET", "HEAD"}
        if not health:
            authorization = headers.getlist("authorization")
            if len(authorization) != 1 or not hmac.compare_digest(authorization[0].encode(), self.authorization):
                await self.reject(scope, receive, send, 401, "AUTHENTICATION_REQUIRED")
                return
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origins = headers.getlist("origin")
            if (
                headers.get("sec-fetch-site") == "cross-site"
                or len(origins) > 1
                or (origins and origins[0] not in self.origins)
            ):
                await self.reject(scope, receive, send, 403, "ORIGIN_NOT_ALLOWED")
                return
        upload = scope["method"] == "POST" and scope["path"] == "/api/v1/generations"
        limit = self.settings.upload_max_bytes + 256 * 1024 if upload else self.settings.api_json_max_bytes
        lengths = headers.getlist("content-length")
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                    raise ValueError("Invalid content length")
                size = int(lengths[0])
            except ValueError:
                await self.reject(scope, receive, send, 400, "INVALID_CONTENT_LENGTH")
                return
            if size > limit:
                await self.reject(scope, receive, send, 413, "UPLOAD_TOO_LARGE" if upload else "REQUEST_TOO_LARGE")
                return
        total = 0
        body_finished = False
        deadline = asyncio.get_running_loop().time() + self.settings.api_request_timeout_seconds

        async def bounded_receive() -> Message:
            nonlocal total, body_finished
            if body_finished:
                return await receive()
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                if remaining <= 0:
                    raise TimeoutError
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError as exc:
                raise BodyRejected(408, "REQUEST_TIMEOUT", "Request reception timed out") from exc
            if message["type"] == "http.request":
                body_finished = not message.get("more_body", False)
                total += len(message.get("body", b""))
                if total > limit:
                    raise BodyRejected(
                        413,
                        "UPLOAD_TOO_LARGE" if upload else "REQUEST_TOO_LARGE",
                        "Request body exceeds its byte budget",
                    )
            return message

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                outgoing = MutableHeaders(scope=message)
                outgoing["X-Content-Type-Options"] = "nosniff"
                outgoing["Cache-Control"] = "private, no-store"
                outgoing["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, bounded_receive, secure_send)
