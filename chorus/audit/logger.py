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
    """One row of the §76 BDSG audit log.

    Attributes:
        user: Authenticated identity that invoked the tool.
        tool_name: Registered tool name (e.g. ``"posts_mentioning"``).
        params: Caller-supplied parameters; serialized to JSON for storage.
        entities_touched: Canonical entity ids the tool resolved while
            running. Used for downstream subject-access requests.
        result_count: Number of result rows the tool returned.
        duration_ms: Wall-clock duration of the tool call in milliseconds.
        status: Final outcome of the call (``"ok"``, ``"denied"``, or
            ``"error"``).
        error_message: Type-prefixed exception message when ``status``
            is ``"error"``; ``None`` otherwise.
    """

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
    """Mutable handle yielded by :meth:`AuditLogger.time_tool`.

    The wrapped block fills these fields as it runs; the logger reads
    them at exit time to build the audit row.

    Attributes:
        entities_touched: Canonical entity ids the tool resolved.
        result_count: Number of result rows the tool produced.
        status: Final outcome (defaults to ``"ok"``, overridden on error).
        error_message: Set automatically when the wrapped block raises.
    """

    entities_touched: list[str] = field(default_factory=list)
    result_count: int = 0
    status: Status = "ok"
    error_message: str | None = None


class AuditLogger:
    """Append-only SQLite-backed audit log.

    Writes one row per tool invocation. The on-disk schema enforces
    append-only semantics via triggers (see ``schema.sql``); this class
    only exposes inserts.
    """

    def __init__(self, db_path: Path) -> None:
        """Create the logger and ensure the parent directory exists.

        Args:
            db_path: Path to the SQLite database file. The parent
                directory is created if missing; the database itself is
                created on first :meth:`init_schema` call.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with WAL journaling enabled.

        Each call opens a fresh connection; SQLite connections are
        cheap and this avoids cross-thread sharing issues with the
        FastAPI worker pool.

        Returns:
            An autocommit connection (``isolation_level=None``) with
            ``WAL`` journaling and ``NORMAL`` synchronous mode applied.
        """
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_schema(self) -> None:
        """Apply ``schema.sql`` to the audit database.

        Idempotent: ``schema.sql`` uses ``CREATE ... IF NOT EXISTS`` for
        the table and its triggers, so repeated calls are safe.
        """
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(ddl)

    def record(self, r: AuditRecord) -> int:
        """Insert one audit row and return its rowid.

        The timestamp is stamped server-side at insert time, not pulled
        from ``r``, so callers cannot backdate entries.

        Args:
            r: Populated :class:`AuditRecord` describing the call.

        Returns:
            The SQLite ``rowid`` of the newly inserted row.
        """
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
        """Time a tool invocation and write the audit row at exit.

        Yields a mutable :class:`_Slot` the wrapped block fills with
        entity ids and a result count. Duration is measured with a
        monotonic clock. If the block raises, the exception is
        re-raised after the row is written with ``status='error'`` and
        a captured message.

        Args:
            user: Authenticated identity invoking the tool.
            tool_name: Registered tool name to record.
            params: Caller-supplied parameters; serialized to JSON.

        Yields:
            The :class:`_Slot` the wrapped block should populate before
            returning.

        Raises:
            Exception: Re-raises whatever the wrapped block raised. The
                audit row is still written.
        """
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
