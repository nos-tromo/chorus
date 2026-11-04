"""A post first written as a reply's parent reference is filled from its own row.

A reply's write MERGEs its parent as a bare ``uuid`` when the parent is not in
the graph yet. When the parent's own row follows (later in the same batch, or
in a later run), the writer must still give it its text, ``ingested_at`` and
``retention_until``; otherwise it would never expire. A post that has been
ingested from its own row keeps its first values on every re-ingest. All
values are invented.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from neo4j import Driver
from typing_extensions import override

from chorus.utils.env_cfg import RetentionConfig
from tests.ingestion._fakes import FakeAdapter

_FIRST = datetime(2026, 3, 1, tzinfo=UTC)
_RETENTION = RetentionConfig(default_days=30)


def _post(driver: Driver, uuid: str) -> dict[str, Any]:
    """Return a post's text, ingestion time and deadline.

    Args:
        driver: Open Neo4j driver.
        uuid: The post's uuid.

    Returns:
        ``{"text", "ingested_at", "retention_until"}`` with Python datetimes.
    """
    with driver.session() as s:
        record = s.run(
            "MATCH (p:Post {uuid: $uuid}) RETURN p.text AS text, p.ingested_at AS ingested_at, "
            "p.retention_until AS retention_until",
            uuid=uuid,
        ).single()
    assert record is not None, uuid
    row = record.data()
    for key in ("ingested_at", "retention_until"):
        if row[key] is not None:
            row[key] = row[key].to_native()
    return row


class _ReplyFirstAdapter(FakeAdapter):
    """Lists each reply before the comment or message it replies to."""

    @override
    def fetch_comments(self, since: Any) -> Iterable[dict[str, Any]]:
        base = {"Network": "linkedin", "Posting ID": "post-net-1"}
        yield {
            **base,
            "UUID": "c-reply",
            "Comment ID": "comment-net-reply",
            "Text Content": "a reply",
            "Author ID": "a-2",
            "Parent Comment ID": "comment-net-parent",
        }
        yield {
            **base,
            "UUID": "c-parent",
            "Comment ID": "comment-net-parent",
            "Text Content": "the parent comment",
            "Author ID": "a-3",
        }

    @override
    def fetch_messages(self, since: Any) -> Iterable[dict[str, Any]]:
        base = {"Chat ID": "chat-1", "Network": "signal"}
        yield {**base, "UUID": "m-reply", "Sender": "a-1", "Text": "a reply", "Reply To": "m-parent"}
        yield {**base, "UUID": "m-parent", "Sender": "a-2", "Text": "the parent message"}


def test_parent_listed_after_its_reply_gets_text_and_deadline(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comment and a message whose reply came first are filled, and expire with their batch."""
    monkeypatch.setenv("NER_ENABLED", "false")
    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()
    run_once(_ReplyFirstAdapter(), migrated_driver, raw, _RETENTION, ingested_at=_FIRST)

    deadline = _FIRST + timedelta(days=30)
    assert _post(migrated_driver, "c-parent") == {
        "text": "the parent comment",
        "ingested_at": _FIRST,
        "retention_until": deadline,
    }
    assert _post(migrated_driver, "m-parent") == {
        "text": "the parent message",
        "ingested_at": _FIRST,
        "retention_until": deadline,
    }
    with migrated_driver.session() as s:
        links = s.run(
            "MATCH (r:Post)-[:REPLIES_TO]->(p:Post) RETURN r.uuid AS reply, p.uuid AS parent ORDER BY reply"
        ).data()
    assert links == [{"reply": "c-reply", "parent": "c-parent"}, {"reply": "m-reply", "parent": "m-parent"}]


def test_posting_first_written_as_a_parent_reference_is_filled(migrated_driver: Driver) -> None:
    """A comment written before its posting leaves a reference the posting's row then fills."""
    from chorus.ingestion import comments, postings

    comments.write(
        migrated_driver,
        comments.from_row(
            {
                "UUID": "c-1",
                "Author ID": "a-2",
                "Network": "linkedin",
                "Text Content": "first",
                "Parent Posting UUID": "p-late",
            },
            _RETENTION,
            _FIRST,
        ),
    )
    assert _post(migrated_driver, "p-late")["ingested_at"] is None

    later = _FIRST + timedelta(days=2)
    postings.write(
        migrated_driver,
        postings.from_row(
            {"UUID": "p-late", "Author ID": "a-1", "Network": "linkedin", "Text Content": "the posting"},
            _RETENTION,
            later,
        ),
    )
    assert _post(migrated_driver, "p-late") == {
        "text": "the posting",
        "ingested_at": later,
        "retention_until": later + timedelta(days=30),
    }


def test_reingest_keeps_the_first_values_and_deadline(migrated_driver: Driver) -> None:
    """A post ingested from its own row is never rewritten, so its deadline holds."""
    from chorus.ingestion import comments, messages, postings

    later = _FIRST + timedelta(days=10)
    for when, text in ((_FIRST, "first"), (later, "edited")):
        postings.write(
            migrated_driver,
            postings.from_row(
                {"UUID": "p-1", "Author ID": "a-1", "Network": "linkedin", "Text Content": text}, _RETENTION, when
            ),
        )
        comments.write(
            migrated_driver,
            comments.from_row(
                {
                    "UUID": "c-1",
                    "Author ID": "a-2",
                    "Network": "linkedin",
                    "Text Content": text,
                    "Parent Posting UUID": "p-1",
                },
                _RETENTION,
                when,
            ),
        )
        messages.write(
            migrated_driver,
            messages.from_row(
                {"UUID": "m-1", "Chat ID": "chat-1", "Sender": "a-1", "Network": "signal", "Text": text},
                _RETENTION,
                when,
            ),
        )

    for uuid in ("p-1", "c-1", "m-1"):
        assert _post(migrated_driver, uuid) == {
            "text": "first",
            "ingested_at": _FIRST,
            "retention_until": _FIRST + timedelta(days=30),
        }
