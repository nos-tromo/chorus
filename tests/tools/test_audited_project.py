"""@audited project attribution tests (ADR 0017)."""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import BaseModel


class _In(BaseModel):
    q: str = "x"


class _Out(BaseModel):
    rows: list[str] = []


def _last_row(audit: Any) -> dict[str, Any]:
    conn = sqlite3.connect(audit.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone())
    finally:
        conn.close()


def test_audited_wrapper_stamps_project_on_audit_row(in_memory_audit: Any) -> None:
    """The wrapper forwards ``project`` to the audit row, not to the tool."""
    from chorus.tools._audit import audited

    seen_kwargs: dict[str, Any] = {}

    @audited
    def fake_tool(driver: Any, params: _In, **kwargs: Any) -> _Out:
        seen_kwargs.update(kwargs)
        return _Out(rows=["a"])

    out = fake_tool(object(), _In(), user="u", audit=in_memory_audit, project="alpha")

    assert out.rows == ["a"]
    assert _last_row(in_memory_audit)["project"] == "alpha"
    assert "project" not in seen_kwargs  # tool functions stay untouched


def test_audited_wrapper_defaults_project(in_memory_audit: Any) -> None:
    """Unconverted call sites keep working and stamp ``default``."""
    from chorus.tools._audit import audited

    @audited
    def fake_tool(driver: Any, params: _In, **kwargs: Any) -> _Out:
        return _Out()

    fake_tool(object(), _In(), user="u", audit=in_memory_audit)

    assert _last_row(in_memory_audit)["project"] == "default"
