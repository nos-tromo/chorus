"""Raw upstream row store (SQLite under ``CHORUS_HOME``).

Captures the upstream rows verbatim before parsing into DTOs. Useful for
re-ingestion when DTO schemas or extraction models change, and as the
single source of truth for "what did the upstream tell us, exactly."
Writing to it is part of an ingestion pass's ``collect`` audit event
(per-table row counts).

The retention sweeper deletes the rows of the posts it deletes, and rows
of the post tables that no post holds once they are older than the
retention period; ``profiles`` and ``connections`` rows are author data
and are kept (ADR 0006, ``docs/retention.md``).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

Kind = Literal["postings", "comments", "messages", "profiles", "connections"]

#: The tables whose rows are posts. ``profiles`` and ``connections`` rows
#: describe authors, whose data is kept indefinitely (ADR 0006).
POST_KINDS: Final[tuple[Kind, ...]] = ("postings", "comments", "messages")

# Values bound per statement, well below SQLite's limit on host parameters.
_CHUNK: Final = 500


@dataclass(frozen=True)
class RawRowRef:
    """One raw row by reference: never its payload.

    Attributes:
        id: The row's id in ``raw_rows``.
        kind: The upstream table it came from.
        uuid: Its ``UUID`` cell, ``None`` when the row had none.
        fetched_at: When chorus received it, as stored (ISO 8601, UTC).
    """

    id: int
    kind: str
    uuid: str | None
    fetched_at: str


def _chunks(values: Sequence[Any]) -> Iterator[Sequence[Any]]:
    """Split values into slices small enough to bind in one statement.

    Args:
        values: The values to bind.

    Yields:
        Consecutive slices of at most :data:`_CHUNK` values.
    """
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def zero_counts() -> dict[str, int]:
    """Return a zero count for each of :data:`POST_KINDS`.

    Returns:
        ``{"postings": 0, "comments": 0, "messages": 0}``.
    """
    return {str(kind): 0 for kind in POST_KINDS}


def _timestamp(value: datetime) -> str:
    """Format a time the way ``fetched_at`` stores it, so the two compare as text.

    Args:
        value: A timezone-aware time.

    Returns:
        The UTC ISO 8601 form with milliseconds, e.g.
        ``2026-01-31T12:00:00.000+00:00``.
    """
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


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
    """SQLite-backed store for raw upstream rows.

    One row per upstream record, stored as JSON. Acts as the canonical
    record of "what the upstream told us, exactly" — independent from
    the graph projection so re-ingestion is possible without re-fetching.
    """

    def __init__(self, db_path: Path) -> None:
        """Create the store and ensure the parent directory exists.

        Args:
            db_path: Path to the SQLite database file. The parent
                directory is created if missing; the database itself is
                created on first :meth:`init_schema` call.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with WAL journaling enabled.

        Returns:
            An autocommit connection (``isolation_level=None``) with
            ``WAL`` journaling applied for concurrent ingestion workers.
        """
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def init_schema(self) -> None:
        """Apply the ``raw_rows`` table and indexes to the database.

        Idempotent — the underlying DDL uses ``CREATE ... IF NOT EXISTS``
        so this is safe to call on every boot.
        """
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def write_batch(self, kind: Kind, rows: Iterable[dict[str, Any]]) -> int:
        """Persist a batch of raw upstream rows to the store.

        The ``UUID`` column is extracted out of each row for indexable
        lookups; the full row (including ``UUID``) is also kept verbatim
        in the JSON payload.

        Args:
            kind: Upstream table the rows belong to.
            rows: Raw rows to persist. Consumed once.

        Returns:
            Number of rows written.
        """
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        # ``ensure_ascii=False`` stores non-ASCII (e.g. Arabic post text) as
        # readable UTF-8 in the payload rather than ``\uXXXX`` escapes; SQLite
        # stores UTF-8 and ``json.loads`` round-trips either way.
        params = [
            (kind, row.get("UUID"), json.dumps(row, default=str, sort_keys=True, ensure_ascii=False), now)
            for row in rows
        ]
        if not params:
            return 0
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO raw_rows (kind, uuid, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
                params,
            )
        return len(params)

    @contextmanager
    def reading(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection for reads, closed afterwards.

        Args:
            read_only: Open the file read-only, so nothing (not even an
                empty database) is ever written; the file must exist.

        Yields:
            An open connection.
        """
        if read_only:
            conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside an immediate write transaction.

        The transaction commits when the block completes and rolls back when
        it raises, so a caller can make its last step (an audit record, say)
        the condition for the commit. A failing ``COMMIT`` raises from the
        ``with`` statement.

        Yields:
            The connection, with the write lock held.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def has_table(conn: sqlite3.Connection) -> bool:
        """Return whether the database holds the ``raw_rows`` table.

        Args:
            conn: An open connection.

        Returns:
            ``True`` once :meth:`init_schema` has run on the file.
        """
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'raw_rows'").fetchone()
        return row is not None

    @staticmethod
    def count_by_kind(conn: sqlite3.Connection) -> dict[str, int]:
        """Count the stored rows per upstream table.

        Args:
            conn: An open connection.

        Returns:
            The row count of each table that has rows.
        """
        return {str(kind): int(n) for kind, n in conn.execute("SELECT kind, count(*) FROM raw_rows GROUP BY kind")}

    @staticmethod
    def count_post_rows(conn: sqlite3.Connection, uuids: Sequence[str]) -> dict[str, int]:
        """Count the rows that hold the given posts, per post table.

        Args:
            conn: An open connection.
            uuids: Post uuids.

        Returns:
            A count for each of :data:`POST_KINDS`.
        """
        counts = zero_counts()
        for chunk in _chunks(uuids):
            marks = ",".join("?" * len(chunk))
            query = (
                f"SELECT kind, count(*) FROM raw_rows WHERE kind IN ('postings','comments','messages') "
                f"AND uuid IN ({marks}) GROUP BY kind"
            )
            for kind, n in conn.execute(query, tuple(chunk)):
                counts[str(kind)] += int(n)
        return counts

    @staticmethod
    def post_rows_received_before(
        conn: sqlite3.Connection, received_before: datetime, *, after_id: int, limit: int
    ) -> list[RawRowRef]:
        """Page through the post rows received before a time, by row id.

        Args:
            conn: An open connection.
            received_before: Only rows whose ``fetched_at`` is earlier.
            after_id: Only rows with a larger id (the last id of the
                previous page; ``0`` for the first).
            limit: The most rows to return.

        Returns:
            The rows by reference, in id order.
        """
        query = (
            "SELECT id, kind, uuid, fetched_at FROM raw_rows WHERE kind IN ('postings','comments','messages') "
            "AND fetched_at < ? AND id > ? ORDER BY id LIMIT ?"
        )
        return [
            RawRowRef(id=int(row_id), kind=str(kind), uuid=None if uuid is None else str(uuid), fetched_at=str(at))
            for row_id, kind, uuid, at in conn.execute(query, (_timestamp(received_before), after_id, limit))
        ]

    @staticmethod
    def delete_post_rows(conn: sqlite3.Connection, uuids: Sequence[str]) -> dict[str, int]:
        """Delete every row that holds one of the given posts.

        A post re-ingested several times has a row per ingestion; all go.

        Args:
            conn: A connection inside :meth:`transaction`.
            uuids: Post uuids.

        Returns:
            The number of rows deleted for each of :data:`POST_KINDS`.
        """
        deleted = zero_counts()
        for chunk in _chunks(uuids):
            marks = ",".join("?" * len(chunk))
            for kind in POST_KINDS:
                cur = conn.execute(f"DELETE FROM raw_rows WHERE kind = ? AND uuid IN ({marks})", (kind, *chunk))
                deleted[kind] += cur.rowcount
        return deleted

    @staticmethod
    def delete_rows(conn: sqlite3.Connection, rows: Sequence[RawRowRef]) -> dict[str, int]:
        """Delete rows by id.

        Args:
            conn: A connection inside :meth:`transaction`.
            rows: The rows to delete.

        Returns:
            The number of rows deleted for each of :data:`POST_KINDS`.
        """
        deleted = zero_counts()
        for kind in POST_KINDS:
            ids = [row.id for row in rows if row.kind == kind]
            for chunk in _chunks(ids):
                marks = ",".join("?" * len(chunk))
                cur = conn.execute(f"DELETE FROM raw_rows WHERE kind = ? AND id IN ({marks})", (kind, *chunk))
                deleted[kind] += cur.rowcount
        return deleted
