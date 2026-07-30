"""GET /whoami endpoint: signed-in identity for the SPA's AppHeader."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    """Minimal app with only the whoami router (no lifespan, no Neo4j)."""
    from chorus.api.routers import whoami as whoami_router

    app = FastAPI()
    app.include_router(whoami_router.router)
    return app


def test_whoami_returns_the_trusted_header_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A present trusted header is echoed back as ``username``; no display name header means None."""
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice", "display_name": None}


def test_whoami_includes_display_name_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """``X-Auth-Name`` (decorative Authelia displayname) is surfaced as ``display_name``."""
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice", "X-Auth-Name": "Alice Example"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice", "display_name": "Alice Example"}


def test_whoami_falls_back_to_default_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no header, the configured dev default identity is returned and display_name is None."""
    monkeypatch.setenv("CHORUS_DEFAULT_IDENTITY", "test-operator")
    client = TestClient(_build_app())
    resp = client.get("/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"username": "test-operator", "display_name": None}


def test_whoami_fails_closed_without_header_or_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No header and no configured fallback means 401, like every other authenticated endpoint."""
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami")
    assert resp.status_code == 401
