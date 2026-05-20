"""§76 BDSG audit log.

Every tool invocation produces exactly one audit row. Use
`AuditLogger.time_tool(...)` as a context manager — it writes a row even
when the wrapped block raises, with `status='error'` and the exception
message captured.

This logger is separate from the operational `loguru` sink. Do not route
audit records through loguru, and do not route operational logs into this
table.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


Status = Literal["ok", "denied", "error"]


class AuditRecord(BaseModel):
    user: str
    tool_name: str
    params: dict[str, Any]
    entities_touched: list[str] = []
    result_count: int = 0
    duration_ms: int = 0
    status: Status = "ok"
    error_message: str | None = None


@dataclass
class _Slot:
    """Mutable handle yielded by `time_tool` so callers can fill in fields."""

    entities_touched: list[str] = field(default_factory=list)
    result_count: int = 0
    status: Status = "ok"
    error_message: str | None = None


class AuditLogger:
    """Append-only SQLite-backed audit log."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_schema(self) -> None:
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(ddl)

    def record(self, r: AuditRecord) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log
                    (ts, user, tool_name, params_json, entities_touched_json,
                     result_count, duration_ms, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    r.user,
                    r.tool_name,
                    json.dumps(r.params, default=str, sort_keys=True),
                    json.dumps(r.entities_touched),
                    r.result_count,
                    r.duration_ms,
                    r.status,
                    r.error_message,
                ),
            )
            row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    @contextmanager
    def time_tool(
        self,
        user: str,
        tool_name: str,
        params: dict[str, Any],
    ) -> Iterator[_Slot]:
        slot = _Slot()
        start = time.perf_counter()
        try:
            yield slot
        except Exception as exc:
            slot.status = "error"
            slot.error_message = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self.record(
                AuditRecord(
                    user=user,
                    tool_name=tool_name,
                    params=params,
                    entities_touched=slot.entities_touched,
                    result_count=slot.result_count,
                    duration_ms=duration_ms,
                    status=slot.status,
                    error_message=slot.error_message,
                )
            )
