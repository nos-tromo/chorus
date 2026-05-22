"""Raw upstream row store (separate SQLite from the audit log).

Captures the upstream rows verbatim before parsing into DTOs. Useful for
re-ingestion when DTO schemas or extraction models change, and as the
single source of truth for "what did the upstream tell us, exactly."

Retention is independent from `audit_log` retention; see
`docs/retention.md`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal


Kind = Literal["postings", "comments", "messages", "profiles", "connections"]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_rows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('postings','comments','messages','profiles','connections')),
  uuid TEXT,
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_kind_fetched ON raw_rows(kind, fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_uuid ON raw_rows(uuid);
"""


class RawStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def write_batch(self, kind: Kind, rows: Iterable[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        params = [
            (kind, row.get("UUID"), json.dumps(row, default=str, sort_keys=True), now)
            for row in rows
        ]
        if not params:
            return 0
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO raw_rows (kind, uuid, payload_json, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                params,
            )
        return len(params)
