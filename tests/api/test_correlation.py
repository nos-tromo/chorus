"""RequestIdMiddleware: the request's cleaned X-Request-ID is in context while it is served."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chorus.api.correlation import RequestIdMiddleware
from chorus.inference.correlation import REQUEST_ID


def _client() -> TestClient:
    """A minimal app whose one sync route echoes the id in context."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/probe")
    def probe() -> dict[str, str | None]:
        return {"request_id": REQUEST_ID.get()}

    return TestClient(app)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-Request-ID": "8d7c2a4e-0f1b-4c3d-9e8f-123456789abc"}, "8d7c2a4e-0f1b-4c3d-9e8f-123456789abc"),
        ({"X-Request-ID": "not a sane id"}, None),
        ({}, None),
    ],
    ids=["uuid", "junk", "absent"],
)
def test_middleware_sets_the_cleaned_id(headers: dict[str, str], expected: str | None) -> None:
    """A sync endpoint (run on the threadpool) sees the cleaned id; junk is dropped."""
    resp = _client().get("/probe", headers=headers)
    assert resp.json() == {"request_id": expected}
    assert REQUEST_ID.get() is None


def test_main_app_installs_the_middleware() -> None:
    """The production app carries the middleware."""
    from chorus.api import main

    assert any(m.cls is RequestIdMiddleware for m in main.app.user_middleware)
