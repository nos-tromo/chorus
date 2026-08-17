"""Audit-log project attribution tests (ADR 0017)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _rows(audit: Any) -> list[dict[str, Any]]:
    """Read all audit rows as dicts, oldest first."""
    conn = sqlite3.connect(audit.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def test_record_persists_project_column(in_memory_audit: Any) -> None:
    """``AuditRecord.project`` lands in the ``project`` column."""
    from chorus.audit.logger import AuditRecord

    in_memory_audit.record(AuditRecord(user="u", tool_name="t", params={}, project="alpha"))

    assert _rows(in_memory_audit)[0]["project"] == "alpha"


def test_time_tool_stamps_project(in_memory_audit: Any) -> None:
    """``time_tool`` forwards its project onto the written row."""
    with in_memory_audit.time_tool("u", "t", {}, project="beta"):
        pass

    assert _rows(in_memory_audit)[0]["project"] == "beta"


def test_project_defaults_to_default(in_memory_audit: Any) -> None:
    """Unthreaded call sites stamp the compat project ``default``."""
    with in_memory_audit.time_tool("u", "t", {}):
        pass

    assert _rows(in_memory_audit)[0]["project"] == "default"


def test_init_schema_upgrades_legacy_table(tmp_path: Path) -> None:
    """A pre-ADR-0017 audit DB gains the ``project`` column on init.

    Per-project DBs are created fresh, but a legacy shared
    ``audit.sqlite`` opened during the transition must not break on
    insert.
    """
    from chorus.audit.logger import AuditLogger, AuditRecord

    db = tmp_path / "audit.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          user TEXT NOT NULL,
          tool_name TEXT NOT NULL,
          params_json TEXT NOT NULL,
          entities_touched_json TEXT NOT NULL,
          result_count INTEGER NOT NULL,
          duration_ms INTEGER NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('ok','denied','error')),
          error_message TEXT
        );
        """
    )
    conn.close()

    audit = AuditLogger(db)
    audit.init_schema()
    audit.record(AuditRecord(user="u", tool_name="t", params={}))

    assert _rows(audit)[0]["project"] == "default"
