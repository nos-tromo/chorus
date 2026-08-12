"""Tools-router project routing tests (ADR 0017)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


class _In(BaseModel):
    q: str = "x"


class _Out(BaseModel):
    marker: str


def test_tools_route_uses_active_project_driver(
    chorus_env: Path, monkeypatch: pytest.MonkeyPatch, in_memory_audit: Any
) -> None:
    """POST /tools/{name} runs against the active project's driver/audit."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")

    from chorus.api.routers import tools as tools_router
    from chorus.tools._audit import ToolSpec, audited

    calls: dict[str, Any] = {}

    @audited
    def fake_tool(driver: Any, params: _In, **kwargs: Any) -> _Out:
        calls["driver"] = driver
        return _Out(marker="ok")

    monkeypatch.setitem(
        tools_router.TOOLS,
        "fake_tool",
        ToolSpec(name="fake_tool", input_model=_In, output_model=_Out, run=fake_tool, description="d"),
    )

    app = FastAPI()
    app.include_router(tools_router.router)
    driver_a, driver_b = object(), object()
    app.state.drivers = {"alpha": driver_a, "beta": driver_b}
    app.state.audits = {"alpha": in_memory_audit, "beta": in_memory_audit}

    r = TestClient(app).post(
        "/tools/fake_tool",
        json={"q": "x"},
        headers={
            "X-Auth-User": "analyst",
            "X-Auth-Projects": "alpha,beta",
            "X-Chorus-Project": "beta",
        },
    )

    assert r.status_code == 200
    assert calls["driver"] is driver_b

    import sqlite3

    conn = sqlite3.connect(in_memory_audit.db_path)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone())
    conn.close()
    assert row["project"] == "beta"
    assert row["user"] == "analyst"
