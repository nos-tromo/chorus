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


def test_utf8_bom_does_not_pollute_first_column_name(tmp_path: Path) -> None:
    r"""A leading UTF-8 BOM is stripped so the first header is clean.

    Excel-style CSV exports frequently prepend ``\ufeff`` to the file.
    With plain ``utf-8`` decoding that codepoint ends up glued to the
    first header (e.g. ``"\ufeffUUID"``), and every downstream lookup
    of ``row["UUID"]`` raises KeyError. ``utf-8-sig`` strips it.
    """
    path = tmp_path / "postings.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\xef\xbb\xbfUUID,Text Content,Timestamp,Crawled at,Author ID,Network\n"
        b"p-1,hi,2026-05-01T10:00:00+00:00,2026-05-02T10:00:00+00:00,a-1,linkedin\n"
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    rows = list(FileUpstreamAdapter(tmp_path).fetch_postings(None))
    assert len(rows) == 1
    assert "UUID" in rows[0]
    assert rows[0]["UUID"] == "p-1"


def test_semicolon_delimited_csv_is_parsed_correctly(tmp_path: Path) -> None:
    """Semicolon-delimited CSVs (European-style exports) are auto-detected.

    The upstream vendor occasionally delivers tables with ``;`` as the
    delimiter and double-quoted fields. The adapter sniffs the dialect
    from the file header so commas and semicolons both work without
    operator intervention.
    """
    path = tmp_path / "postings.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '"UUID";"Text Content";"Timestamp";"Crawled at";"Author ID";"Network"\n'
        '"p-1";"hello";"2026-05-01T10:00:00+00:00";"2026-05-02T10:00:00+00:00";"a-1";"linkedin"\n',
        encoding="utf-8",
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    rows = list(FileUpstreamAdapter(tmp_path).fetch_postings(None))
    assert len(rows) == 1
    assert rows[0]["UUID"] == "p-1"
    assert rows[0]["Text Content"] == "hello"
    assert rows[0]["Timestamp"] == "2026-05-01T10:00:00+00:00"


def test_overflow_columns_are_dropped_not_yielded_under_none_key(
    tmp_path: Path,
) -> None:
    """Rows wider than the header don't surface a ``None``-keyed entry.

    ``csv.DictReader`` puts fields past the header width under the
    ``restkey`` (default ``None``). Leaking that into the row dict
    breaks ``raw_store.write_batch`` because ``json.dumps(...,
    sort_keys=True)`` can't compare ``None`` against string keys —
    so the adapter must strip it at the boundary.
    """
    path = tmp_path / "postings.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Hand-rolled CSV with one extra column per data row beyond the header.
    path.write_text(
        "UUID,Text Content,Timestamp,Crawled at,Author ID,Network\n"
        "p-1,hi,2026-05-01T10:00:00+00:00,2026-05-02T10:00:00+00:00,a-1,linkedin,extra-overflow\n",
        encoding="utf-8",
    )
    from chorus.ingestion.upstream import FileUpstreamAdapter

    rows = list(FileUpstreamAdapter(tmp_path).fetch_postings(None))
    assert len(rows) == 1
    assert None not in rows[0]
    assert rows[0]["UUID"] == "p-1"


def test_missing_file_yields_empty_iterator(tmp_path: Path) -> None:
    """A missing per-table file is not an error — yields nothing."""
    from chorus.ingestion.upstream import FileUpstreamAdapter

    adapter = FileUpstreamAdapter(tmp_path)
    assert list(adapter.fetch_postings(None)) == []
    assert list(adapter.fetch_comments(None)) == []
    assert list(adapter.fetch_messages(None)) == []
    assert list(adapter.fetch_profiles(None)) == []


def test_fetch_connections_reads_csv(tmp_path: Path) -> None:
    """``connections.csv`` is read row-by-row with the upstream column names verbatim (ADR 0007)."""
    from chorus.ingestion.upstream import FileUpstreamAdapter

    _write_csv(
        tmp_path / "connections.csv",
        [
            {
                "Network Object ID": "row-1",
                "Network Object ID selected conn. User": "target-1",
                "Vanity Name": "alice",
                "Vanity Name selected conn. User": "afdimbundestag",
                "Friend": "No",
                "Follower": "Yes",
                "Following": "No",
                "Crawled at": "2026-05-26T02:31:43+00:00",
                "Network": "Instagram",
            }
        ],
    )

    rows = list(FileUpstreamAdapter(tmp_path).fetch_connections(None))
    assert len(rows) == 1
    assert rows[0]["Network Object ID"] == "row-1"
    assert rows[0]["Follower"] == "Yes"


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
