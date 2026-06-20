"""GET /config endpoint: language + ingestion flag + version."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    """Minimal app with only the config router (no lifespan, no Neo4j)."""
    from chorus.api.routers import config as config_router

    app = FastAPI()
    app.include_router(config_router.router)
    return app


def test_config_reports_language_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    client = TestClient(_build_app())
    resp = client.get("/config")  # no auth header
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "de"
    assert body["ingestion_enabled"] is True
    assert isinstance(body["version"], str) and body["version"]


def test_config_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "en"
    assert body["ingestion_enabled"] is False
