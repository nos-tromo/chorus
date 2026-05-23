"""FileUpstreamAdapter: read upstream CSV dumps from a source directory.

The adapter is intentionally thin: it yields ``dict`` rows keyed by the
upstream's *exact* column names so the existing per-table ``from_row``
functions can consume them unchanged. The since filter is per-table —
``Crawled at`` for postings/comments/profiles, ``Timestamp`` for
messages — and over-includes rather than dropping rows when the column
is missing or unparseable.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write ``rows`` to ``path`` using the keys of the first row as headers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_fetch_postings_yields_dicts_with_upstream_column_names(tmp_path: Path) -> None:
    """Postings rows are yielded verbatim, keyed by upstream column names."""
    _write_csv(
        tmp_path / "postings.csv",
        [
            {
                "UUID": "p-1",
                "Text Content": "hello berlin",
                "Timestamp": "2026-05-01T10:00:00+00:00",
                "Crawled at": "2026-05-02T10:00:00+00:00",
                "Author ID": "a-1",
                "Author": "Alice",
                "Network": "linkedin",
                "Tags": "news, politics",
            }
        ],
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    rows = list(FileUpstreamAdapter(tmp_path).fetch_postings(None))
    assert len(rows) == 1
    assert rows[0]["UUID"] == "p-1"
    assert rows[0]["Text Content"] == "hello berlin"
    assert rows[0]["Crawled at"] == "2026-05-02T10:00:00+00:00"
    assert rows[0]["Tags"] == "news, politics"


def test_fetch_messages_uses_timestamp_column(tmp_path: Path) -> None:
    """Messages have no ``Crawled at``; the since filter reads ``Timestamp``."""
    _write_csv(
        tmp_path / "messages.csv",
        [
            {
                "UUID": "m-old",
                "Chat ID": "chat-1",
                "Sender": "a-1",
                "Text": "old",
                "Timestamp": "2025-12-31T00:00:00+00:00",
                "Network": "signal",
            },
            {
                "UUID": "m-new",
                "Chat ID": "chat-1",
                "Sender": "a-1",
                "Text": "new",
                "Timestamp": "2026-05-01T00:00:00+00:00",
                "Network": "signal",
            },
        ],
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    rows = list(FileUpstreamAdapter(tmp_path).fetch_messages(cutoff))
    assert [r["UUID"] for r in rows] == ["m-new"]


def test_fetch_postings_since_filter(tmp_path: Path) -> None:
    """Since filter on postings reads ``Crawled at``."""
    _write_csv(
        tmp_path / "postings.csv",
        [
            {
                "UUID": "p-old",
                "Text Content": "x",
                "Timestamp": "2025-12-30T00:00:00+00:00",
                "Crawled at": "2025-12-31T00:00:00+00:00",
                "Author ID": "a-1",
                "Network": "linkedin",
            },
            {
                "UUID": "p-new",
                "Text Content": "x",
                "Timestamp": "2026-05-01T00:00:00+00:00",
                "Crawled at": "2026-05-02T00:00:00+00:00",
                "Author ID": "a-1",
                "Network": "linkedin",
            },
        ],
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    rows = list(FileUpstreamAdapter(tmp_path).fetch_postings(cutoff))
    assert [r["UUID"] for r in rows] == ["p-new"]


def test_since_filter_overincludes_when_timestamp_unparseable(tmp_path: Path) -> None:
    """Rows whose timestamp can't be parsed are kept under since filtering.

    Silently dropping rows because the cell is malformed loses data; the
    over-include behavior is a deliberate ADR-aligned choice (the
    pipeline favors traceability over aggressive filtering).
    """
    _write_csv(
        tmp_path / "postings.csv",
        [
            {
                "UUID": "p-bogus",
                "Text Content": "x",
                "Timestamp": "2026-05-01T00:00:00+00:00",
                "Crawled at": "not-a-date",
                "Author ID": "a-1",
                "Network": "linkedin",
            },
            {
                "UUID": "p-missing",
                "Text Content": "x",
                "Timestamp": "2026-05-01T00:00:00+00:00",
                "Crawled at": "",
                "Author ID": "a-1",
                "Network": "linkedin",
            },
        ],
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    rows = list(FileUpstreamAdapter(tmp_path).fetch_postings(cutoff))
    assert {r["UUID"] for r in rows} == {"p-bogus", "p-missing"}


def test_missing_file_yields_empty_iterator(tmp_path: Path) -> None:
    """A missing per-table file is not an error — yields nothing."""
    from chorus.ingestion.upstream import FileUpstreamAdapter

    adapter = FileUpstreamAdapter(tmp_path)
    assert list(adapter.fetch_postings(None)) == []
    assert list(adapter.fetch_comments(None)) == []
    assert list(adapter.fetch_messages(None)) == []
    assert list(adapter.fetch_profiles(None)) == []


def test_fetch_connections_still_raises(tmp_path: Path) -> None:
    """Connections is blocked on the upstream schema (ADR 0002)."""
    from chorus.ingestion.upstream import FileUpstreamAdapter

    with pytest.raises(NotImplementedError, match="ADR 0002"):
        list(FileUpstreamAdapter(tmp_path).fetch_connections(None))


def test_profile_row_keys_match_profile_dto_expectations(tmp_path: Path) -> None:
    """Profile rows pass through unchanged so :func:`profiles.from_row` consumes them."""
    _write_csv(
        tmp_path / "profiles.csv",
        [
            {
                "ID": "a-1",
                "UUID": "prof-a-1",
                "Name": "Alice Anderson",
                "Vanity Name": "alice",
                "Profile Type": "person",
                "Network": "linkedin",
                "Bio": "Berlin-based analyst",
                "Date of Birth": "1990-03-15",
                "Tags": "verified, staff",
            }
        ],
    )
    from chorus.ingestion.profiles import from_row
    from chorus.ingestion.upstream import FileUpstreamAdapter

    rows = list(FileUpstreamAdapter(tmp_path).fetch_profiles(None))
    assert len(rows) == 1
    dto = from_row(rows[0])
    assert dto.id == "a-1"
    assert dto.display_name == "Alice Anderson"
