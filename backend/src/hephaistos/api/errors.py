"""Stable HTTP errors shared by API resources and streaming uploads."""

from typing import Any


class ResourceError(Exception):
    def __init__(self, status: int, code: str, detail: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.errors = status, code, detail, errors
