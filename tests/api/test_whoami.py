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
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice"})
    assert resp.status_code == 200
    assert resp.json() == {
        "username": "alice",
        "display_name": None,
        "projects": ["default"],
        "active_project": "default",
    }


def test_whoami_includes_display_name_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """``X-Auth-Name`` (decorative Authelia displayname) is surfaced as ``display_name``."""
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice", "X-Auth-Name": "Alice Example"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "alice"
    assert body["display_name"] == "Alice Example"


def test_whoami_falls_back_to_default_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no header, the configured dev default identity is returned and display_name is None."""
    monkeypatch.setenv("CHORUS_DEFAULT_IDENTITY", "test-operator")
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "test-operator"
    assert body["display_name"] is None


def test_whoami_fails_closed_without_header_or_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No header and no configured fallback means 401, like every other authenticated endpoint."""
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami")
    assert resp.status_code == 401


def test_whoami_compat_mode_single_default_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """In compat mode /whoami reports the implicit default project (ADR 0017)."""
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == ["default"]
    assert body["active_project"] == "default"


def test_whoami_lists_allowed_intersect_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """/whoami reports the claim ∩ configured set and the active selection (ADR 0017)."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta,gamma")
    client = TestClient(_build_app())
    resp = client.get(
        "/whoami",
        headers={
            "X-Auth-User": "alice",
            "X-Auth-Projects": "beta,ghost,alpha",
            "X-Chorus-Project": "beta",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == ["alpha", "beta"]
    assert body["active_project"] == "beta"


def test_whoami_null_active_when_selection_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several allowed projects and no selection reports ``None``, not 400.

    /whoami is the SPA's only source for the project list, so it must
    answer before a project has been chosen — otherwise the switcher can
    never learn what there is to switch between.
    """
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice", "X-Auth-Projects": "alpha,beta"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == ["alpha", "beta"]
    assert body["active_project"] is None


def test_whoami_null_active_on_stale_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A selection outside the claim reports ``None`` rather than 403.

    The SPA replays a remembered project on boot; a claim that has since
    changed must not lock the user out of the picker.
    """
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)
    client = TestClient(_build_app())
    resp = client.get(
        "/whoami",
        headers={
            "X-Auth-User": "alice",
            "X-Auth-Projects": "alpha,beta",
            "X-Chorus-Project": "ghost",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == ["alpha", "beta"]
    assert body["active_project"] is None


def test_whoami_null_active_without_project_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A principal with no usable claim gets an empty list, not a 403."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-User": "alice", "X-Auth-Projects": "ghost"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == []
    assert body["active_project"] is None


def test_whoami_lenient_project_does_not_soften_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lenient project resolution must not swallow the 401 from the principal seam."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Auth-Projects": "alpha,beta"})
    assert resp.status_code == 401
