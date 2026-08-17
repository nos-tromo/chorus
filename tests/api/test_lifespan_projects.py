"""Lifespan + RequestContext wiring tests (ADR 0017)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def test_lifespan_populates_per_project_state(chorus_env: Path) -> None:
    """In compat mode the app boots exactly one project, ``default``.

    ``app.state.drivers`` / ``audits`` / ``projects`` are the per-project
    registries; the audit DB lands under ``projects/default/``.
    """
    from chorus.api.main import app

    with TestClient(app):
        assert app.state.projects == ("default",)
        assert set(app.state.drivers) == {"default"}
        assert set(app.state.audits) == {"default"}
        assert app.state.audits["default"].db_path == chorus_env / "projects" / "default" / "audit.sqlite"
    assert (chorus_env / "projects" / "default" / "audit.sqlite").exists()


def test_resolve_context_returns_project_bound_driver_and_audit(
    chorus_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_context`` picks driver + audit for the active project."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")

    from chorus.api.deps import RequestContext, resolve_context

    app = FastAPI()
    driver_a, driver_b = object(), object()
    audit_a, audit_b = object(), object()
    app.state.drivers = {"alpha": driver_a, "beta": driver_b}
    app.state.audits = {"alpha": audit_a, "beta": audit_b}

    seen: dict[str, Any] = {}

    @app.get("/probe")
    def probe(ctx: RequestContext = Depends(resolve_context)) -> dict[str, str]:  # noqa: B008 — FastAPI DI marker
        seen["ctx"] = ctx
        return {"user": ctx.user, "project": ctx.project}

    r = TestClient(app).get(
        "/probe",
        headers={
            "X-Auth-User": "analyst",
            "X-Auth-Projects": "alpha,beta",
            "X-Chorus-Project": "beta",
        },
    )

    assert r.status_code == 200
    assert r.json() == {"user": "analyst", "project": "beta"}
    assert seen["ctx"].driver is driver_b
    assert seen["ctx"].audit is audit_b
