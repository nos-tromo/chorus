"""Project-claim resolution tests (ADR 0017).

``resolve_project`` is exercised through a throwaway FastAPI app, the
same way the router tests exercise ``resolve_principal``.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient


def _client() -> TestClient:
    """Bare app with one route that echoes the resolved project."""
    from chorus.api.auth.principal import resolve_project

    app = FastAPI()

    @app.get("/echo")
    def echo(project: str = Depends(resolve_project)) -> dict[str, str]:
        return {"project": project}

    return TestClient(app)


def test_compat_mode_returns_default_without_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no CHORUS_PROJECTS configured, every request runs as ``default``."""
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get("/echo")
    assert r.status_code == 200
    assert r.json() == {"project": "default"}


def test_compat_mode_rejects_foreign_active_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting a non-default project in compat mode fails loudly (403).

    Silently serving ``default`` data under a project the caller believes
    exists would be a cross-project confusion bug.
    """
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)

    r = _client().get("/echo", headers={"X-Chorus-Project": "alpha"})
    assert r.status_code == 403


def test_explicit_mode_valid_claim_and_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Active project inside the asserted claim is accepted."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get(
        "/echo",
        headers={"X-Auth-Projects": "alpha,beta", "X-Chorus-Project": "beta"},
    )
    assert r.status_code == 200
    assert r.json() == {"project": "beta"}


def test_explicit_mode_missing_claim_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a gateway claim (and no dev fallback), project access fails closed."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get("/echo", headers={"X-Chorus-Project": "alpha"})
    assert r.status_code == 403


def test_explicit_mode_active_not_in_allowed_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting a project outside the asserted claim is forbidden."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get(
        "/echo",
        headers={"X-Auth-Projects": "alpha", "X-Chorus-Project": "beta"},
    )
    assert r.status_code == 403


def test_explicit_mode_single_allowed_implies_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """A claim of exactly one project needs no explicit selection."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get("/echo", headers={"X-Auth-Projects": "beta"})
    assert r.status_code == 200
    assert r.json() == {"project": "beta"}


def test_explicit_mode_ambiguous_selection_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple allowed projects with no selection is a client error."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get("/echo", headers={"X-Auth-Projects": "alpha,beta"})
    assert r.status_code == 400


def test_default_project_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CHORUS_DEFAULT_PROJECT`` supplies both claim and selection for dev."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.setenv("CHORUS_DEFAULT_PROJECT", "alpha")

    r = _client().get("/echo")
    assert r.status_code == 200
    assert r.json() == {"project": "alpha"}


def test_claim_intersected_with_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A claimed project that is not configured cannot be selected."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha")
    monkeypatch.delenv("CHORUS_DEFAULT_PROJECT", raising=False)

    r = _client().get(
        "/echo",
        headers={"X-Auth-Projects": "alpha,ghost", "X-Chorus-Project": "ghost"},
    )
    assert r.status_code == 403


def test_allowed_projects_intersects_configured_in_config_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """``allowed_projects`` returns the claim ∩ configured, in configured order."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta,gamma")

    from chorus.api.auth.principal import allowed_projects

    app = FastAPI()

    @app.get("/list")
    def list_projects(request: Request) -> dict[str, list[str]]:
        return {"projects": allowed_projects(request)}

    r = TestClient(app).get("/list", headers={"X-Auth-Projects": "gamma,ghost,alpha"})
    assert r.status_code == 200
    assert r.json() == {"projects": ["alpha", "gamma"]}
