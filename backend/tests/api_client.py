"""Authenticated client for existing API behavior tests. Negative auth tests use the raw client."""

from fastapi.testclient import TestClient as BaseClient


class TestClient(BaseClient):
    __test__ = False

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("base_url", "http://localhost")
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", "Bearer security-test-token-at-least-32-characters")
        super().__init__(*args, headers=headers, **kwargs)
