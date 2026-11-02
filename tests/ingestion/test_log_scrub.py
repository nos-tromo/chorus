"""Ingestion log lines name a row by its upstream UUID and an error by its shape.

Upstream rows carry post text and network-side identifiers (account ids,
network post ids, handles); neither may reach the operational log. These
tests need no Neo4j: every case stops before a graph write. All values are
synthetic placeholders.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from loguru import logger
from pydantic import BaseModel

_UUID = "00000000-0000-4000-8000-000000000001"
_TEXT = "PLACEHOLDER-POST-TEXT-0001"
_NETWORK_ID = "placeholder-network-id-0001"
_OTHER_NETWORK_ID = "placeholder-network-id-0002"


@pytest.fixture
def logs() -> Iterator[list[str]]:
    """Capture every loguru record emitted during the test."""
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(str(m)), level="DEBUG")
    try:
        yield captured
    finally:
        logger.remove(sink_id)


def _skip_posting(row: dict[str, Any]) -> dict[str, int]:
    """Run one posting row through ``_parse_or_skip``; assert it was dropped."""
    from chorus.ingestion import postings
    from chorus.ingestion.orchestrator import _parse_or_skip
    from chorus.utils.env_cfg import load_retention_env

    dropped: dict[str, int] = {}
    dto = _parse_or_skip("postings", row, postings.from_row, load_retention_env(), datetime.now(UTC), dropped=dropped)
    assert dto is None
    return dropped


def test_missing_column_logs_uuid_and_column_name(logs: list[str]) -> None:
    """A KeyError names the missing column, never another cell of the row."""
    dropped = _skip_posting({"UUID": _UUID, "Posting ID": _NETWORK_ID, "Text Content": _TEXT, "Network": "net"})

    out = "\n".join(logs)
    assert dropped == {"postings": 1}
    assert f"postings row {_UUID} skipped: missing column 'Author ID'" in out
    assert _TEXT not in out
    assert _NETWORK_ID not in out


def test_unparseable_value_logs_exception_type_only(logs: list[str]) -> None:
    """A ValueError's message quotes the bad cell, so only its type is logged."""
    _skip_posting({"UUID": _UUID, "Author ID": _NETWORK_ID, "Network": "net", "Timestamp": _TEXT})

    out = "\n".join(logs)
    assert f"postings row {_UUID} skipped: ValueError" in out
    assert _TEXT not in out
    assert _NETWORK_ID not in out


def test_validation_error_logs_location_and_type_only(logs: list[str]) -> None:
    """A ValidationError is logged by field and error type with the value redacted."""
    from chorus.ingestion.orchestrator import _parse_or_skip

    class _Dto(BaseModel):
        count: int

    def _build(row: dict[str, Any]) -> _Dto:
        return _Dto(count=row["Count"])

    dropped: dict[str, int] = {}
    assert _parse_or_skip("messages", {"UUID": _UUID, "Count": _TEXT}, _build, dropped=dropped) is None

    out = "\n".join(logs)
    assert dropped == {"messages": 1}
    assert f"messages row {_UUID} skipped:" in out
    assert "('count',)" in out
    assert "int_parsing" in out
    assert "<redacted>" in out
    assert _TEXT not in out


def test_phantom_row_text_in_uuid_cell_is_not_logged(logs: list[str]) -> None:
    """A mis-split multi-line post leaves text in the UUID cell; it is not echoed."""
    _skip_posting({"UUID": f"{_TEXT} continued on the next line", "Author ID": None, "Network": None})

    out = "\n".join(logs)
    assert "postings row <malformed uuid> skipped:" in out
    assert "('network',)" in out
    assert _TEXT not in out


def test_connections_row_has_no_uuid_and_no_network_id_fallback(logs: list[str]) -> None:
    """A row without a UUID is not identified by its network account id instead."""
    from chorus.ingestion import connections
    from chorus.ingestion.orchestrator import _parse_or_skip

    dropped: dict[str, int] = {}
    row = {"Network Object ID": _NETWORK_ID, "ID": _OTHER_NETWORK_ID, "Follower": "Yes"}
    assert _parse_or_skip("connections", row, connections.from_row, dropped=dropped) is None

    out = "\n".join(logs)
    assert "connections row <no uuid> skipped: missing column 'Network Object ID selected conn. User'" in out
    assert _NETWORK_ID not in out
    assert _OTHER_NETWORK_ID not in out


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"UUID": _UUID}, _UUID),
        ({"UUID": _UUID.replace("-", "").upper()}, _UUID.replace("-", "").upper()),
        ({"UUID": f"  {_UUID}  "}, _UUID),
        ({}, "<no uuid>"),
        ({"UUID": None}, "<no uuid>"),
        ({"UUID": "   "}, "<no uuid>"),
        ({"UUID": "p-1"}, "<malformed uuid>"),
        ({"UUID": _TEXT}, "<malformed uuid>"),
        ({"ID": _NETWORK_ID, "Network Object ID": _NETWORK_ID}, "<no uuid>"),
    ],
)
def test_row_ref_logs_only_uuid_shaped_values(row: dict[str, Any], expected: str) -> None:
    """Only a UUID-shaped ``UUID`` cell is used to identify a row in the log."""
    from chorus.ingestion.orchestrator import _row_ref

    assert _row_ref(row) == expected


class _OrphanCommentAdapter:
    """Adapter whose only row is a comment pointing at an unknown posting."""

    def fetch_postings(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        return []

    def fetch_comments(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        return [{"UUID": _UUID, "Posting ID": _NETWORK_ID, "Text Content": _TEXT, "Author ID": "a", "Network": "n"}]

    def fetch_messages(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        return []

    def fetch_profiles(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        return []

    def fetch_connections(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        return []


def test_orphan_comment_logs_uuid_but_not_parent_posting_id(
    logs: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skipped comment is named by its UUID; the network-side Posting ID is not logged."""
    monkeypatch.setenv("NER_ENABLED", "false")
    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_retention_env

    raw = RawStore(tmp_path / "raw.sqlite")
    raw.init_schema()
    driver: Any = None  # the orphan is filtered before any graph write

    result = run_once(_OrphanCommentAdapter(), driver, raw, load_retention_env())

    out = "\n".join(logs)
    assert result["filtered"]["comments"] == 1
    assert f"comment {_UUID} skipped: parent posting not in this batch" in out
    assert _NETWORK_ID not in out
    assert _TEXT not in out


def test_connections_drops_do_not_log_account_ids(logs: list[str]) -> None:
    """Self-loop and flagless rows are dropped with a reason and no account id."""
    from chorus.ingestion.connections import from_row

    target = "Network Object ID selected conn. User"
    assert from_row({"Network Object ID": _NETWORK_ID, target: _NETWORK_ID, "Follower": "Yes"}) is None
    assert from_row({"Network Object ID": _OTHER_NETWORK_ID, target: _NETWORK_ID, "Friend": "No"}) is None

    out = "\n".join(logs)
    assert "connections row dropped: self-loop" in out
    assert "connections row dropped: no Friend/Follower/Following flag set" in out
    assert _NETWORK_ID not in out
    assert _OTHER_NETWORK_ID not in out
