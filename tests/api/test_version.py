"""GET /version endpoint: unauthenticated app release version."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    from chorus.api.routers import config as config_router

    app = FastAPI()
    app.include_router(config_router.router)
    return app


def test_version_returns_package_version() -> None:
    """GET /version returns a non-empty version string."""
    client = TestClient(_build_app())
    resp = client.get("/version")  # unauthenticated
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["version"], str) and body["version"]
