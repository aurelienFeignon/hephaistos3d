"""API tests use a non-production credential; security tests explicitly omit it when needed."""

import pytest


@pytest.fixture(autouse=True)
def api_test_token(monkeypatch):
    monkeypatch.setenv("API_ACCESS_TOKEN", "security-test-token-at-least-32-characters")
