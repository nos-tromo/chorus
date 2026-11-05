"""RawStore persistence — focused unit tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from chorus.ingestion.raw_store import RawStore


def test_write_batch_stores_non_ascii_as_utf8(tmp_path: Path) -> None:
    r"""Raw payloads keep non-ASCII as readable UTF-8, not ``\uXXXX`` escapes.

    The raw store is the "what did the upstream tell us, exactly" record and is
    inspected directly during re-ingestion debugging. Escaping Arabic post text
    to ``\uXXXX`` only makes it unreadable; SQLite stores UTF-8 regardless. The
    stored payload must carry the real characters and round-trip through
    ``json.loads``. A raw Arabic substring being present is itself proof it was
    not ASCII-escaped.
    """
    store = RawStore(tmp_path / "raw.sqlite")
    store.init_schema()

    text = "مرحبا بالعالم"
    written = store.write_batch("postings", [{"UUID": "p-1", "Text Content": text}])
    assert written == 1

    (payload_json,) = (
        sqlite3.connect(store.db_path).execute("SELECT payload_json FROM raw_rows WHERE uuid = ?", ("p-1",)).fetchone()
    )

    assert text in payload_json
    assert json.loads(payload_json)["Text Content"] == text


def _store(tmp_path: Path) -> RawStore:
    """Return a raw store with its schema applied.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The store.
    """
    store = RawStore(tmp_path / "raw.sqlite")
    store.init_schema()
    return store


def test_transaction_rolls_back_when_its_block_raises(tmp_path: Path) -> None:
    """A deletion whose block fails (its audit record, say) leaves every row in place."""
    store = _store(tmp_path)
    store.write_batch("postings", [{"UUID": "p-1"}])

    with pytest.raises(RuntimeError), store.transaction() as conn:
        assert RawStore.delete_post_rows(conn, ["p-1"])["postings"] == 1
        raise RuntimeError("the record could not be written")

    with store.reading() as conn:
        assert RawStore.count_by_kind(conn) == {"postings": 1}


def test_delete_post_rows_takes_every_row_of_a_post_and_no_author_row(tmp_path: Path) -> None:
    """Each ingestion of a post left a row; all go. A profile row with the same cell stays."""
    store = _store(tmp_path)
    store.write_batch("postings", [{"UUID": "p-1"}, {"UUID": "p-1"}, {"UUID": "p-2"}])
    store.write_batch("comments", [{"UUID": "p-1"}])
    store.write_batch("profiles", [{"UUID": "p-1"}])

    with store.transaction() as conn:
        deleted = RawStore.delete_post_rows(conn, ["p-1"])

    assert deleted == {"postings": 2, "comments": 1, "messages": 0}
    with store.reading() as conn:
        assert RawStore.count_by_kind(conn) == {"postings": 1, "profiles": 1}


def test_read_only_reading_cannot_write(tmp_path: Path) -> None:
    """The dry run opens the raw store read-only: a write through it fails."""
    store = _store(tmp_path)
    store.write_batch("postings", [{"UUID": "p-1"}])

    with store.reading(read_only=True) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM raw_rows")

    with store.reading() as conn:
        assert RawStore.count_by_kind(conn) == {"postings": 1}


def test_post_rows_received_before_pages_post_tables_by_id(tmp_path: Path) -> None:
    """Paging covers the three post tables only, in id order, and stops at the receive time."""
    from datetime import UTC, datetime, timedelta

    store = _store(tmp_path)
    store.write_batch("postings", [{"UUID": "p-1"}, {"UUID": "p-2"}])
    store.write_batch("profiles", [{"UUID": "profile-1"}])
    store.write_batch("messages", [{"Text": "no uuid"}])
    later = datetime.now(UTC) + timedelta(days=1)

    with store.reading() as conn:
        first = RawStore.post_rows_received_before(conn, later, after_id=0, limit=2)
        rest = RawStore.post_rows_received_before(conn, later, after_id=first[-1].id, limit=2)
        none = RawStore.post_rows_received_before(conn, datetime.now(UTC) - timedelta(days=1), after_id=0, limit=10)

    assert [(r.kind, r.uuid) for r in first] == [("postings", "p-1"), ("postings", "p-2")]
    assert [(r.kind, r.uuid) for r in rest] == [("messages", None)]
    assert none == []
