"""run_agent project attribution tests (ADR 0017)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any, cast

import pytest


def test_run_agent_threads_project_into_audit(
    monkeypatch: pytest.MonkeyPatch, in_memory_audit: Any, fake_inference: None
) -> None:
    """The parent ``agent_query`` audit row carries the active project."""
    from chorus.agent.loop import run_agent
    from chorus.inference import provider

    final = SimpleNamespace(content="done", tool_calls=None)
    monkeypatch.setattr(provider, "chat_message", lambda messages, **kwargs: final)

    result = run_agent(
        cast("Any", object()),
        in_memory_audit,
        user="analyst",
        project="alpha",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result.answer == "done"
    conn = sqlite3.connect(in_memory_audit.db_path)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM audit_log WHERE tool_name = 'agent_query'").fetchone())
    conn.close()
    assert row["project"] == "alpha"
    assert row["user"] == "analyst"
