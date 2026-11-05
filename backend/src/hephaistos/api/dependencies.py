"""The principal boundary requires the same server credential as the ASGI guard."""

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: str


def current_principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]
) -> Principal:
    if request.url.path == "/api/v1/system/health" and request.method in {"GET", "HEAD"}:
        return Principal("healthcheck")
    secret = request.app.state.api_settings.api_access_token
    if (
        credentials is None
        or secret is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials.encode(), secret.get_secret_value().encode())
    ):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return Principal("local")
