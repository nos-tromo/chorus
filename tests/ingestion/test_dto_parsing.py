"""DTO-level parsing tests for the per-table ``from_row`` helpers.

These cover quirks of real upstream exports — values padded with
whitespace, ISO-8601 variants ``datetime.fromisoformat`` will and
won't accept — that the adapter cannot fix on its own because it
yields rows verbatim.
"""

from __future__ import annotations

from chorus.utils.env_cfg import RetentionConfig


def test_postings_from_row_strips_whitespace_padded_timestamps() -> None:
    """Upstream sometimes pads timestamps with trailing spaces.

    Real export seen in the field: ``'2026-02-13 15:58:07+00<spaces>'``.
    ``datetime.fromisoformat`` rejects the trailing whitespace, so the
    DTO parsers must ``.strip()`` before parsing or every row aborts
    ingestion at ``postings.from_row``.
    """
    from chorus.ingestion.postings import from_row

    row = {
        "UUID": "p-1",
        "Text Content": "hi",
        "Timestamp": "2026-02-13 15:58:07+00" + " " * 80,
        "Crawled at": "  2026-02-14 09:00:00+00:00  ",
        "Author ID": "a-1",
        "Network": "linkedin",
    }
    dto = from_row(row, RetentionConfig(default_days=30))
    assert dto.uuid == "p-1"
    assert dto.timestamp is not None
    assert dto.timestamp.year == 2026
    assert dto.crawled_at is not None
    assert dto.crawled_at.year == 2026


def test_postings_from_row_keeps_row_with_missing_timestamp() -> None:
    """A posting with no parseable Timestamp is kept, not dropped.

    Content creation time is optional upstream; a missing or blank
    ``Timestamp`` must neither abort the run nor drop the row. ``timestamp``
    resolves to ``None`` while the row is still ingested.
    """
    from chorus.ingestion.postings import from_row

    row = {
        "UUID": "p-1",
        "Text Content": "no timestamp",
        "Timestamp": None,
        "Crawled at": "2026-06-01T09:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
    }
    dto = from_row(row, RetentionConfig(default_days=30))

    assert dto.timestamp is None


def test_postings_from_row_keeps_row_with_missing_crawled_at() -> None:
    """A posting with no Crawled at is kept; crawled_at is informational only."""
    from chorus.ingestion.postings import from_row

    row = {
        "UUID": "p-1",
        "Text Content": "no crawl time",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
    }
    dto = from_row(row, RetentionConfig(default_days=30))

    assert dto.crawled_at is None


def test_postings_from_row_anchors_retention_on_ingested_at() -> None:
    """retention_until is measured from the chorus-set ingested_at, not upstream times."""
    from datetime import UTC, datetime

    from chorus.ingestion.postings import from_row

    ingested = datetime(2026, 6, 1, tzinfo=UTC)
    row = {
        "UUID": "p-1",
        "Text Content": "hi",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Crawled at": "2026-03-01T00:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
    }
    dto = from_row(row, RetentionConfig(default_days=30), ingested)

    assert dto.ingested_at == ingested
    # ingested_at (Jun 1) + 30d = Jul 1 — independent of Timestamp / Crawled at.
    assert dto.retention_until == datetime(2026, 7, 1, tzinfo=UTC)


def test_postings_from_row_retention_disabled_yields_none() -> None:
    """With retention disabled, a posting carries no retention_until deadline."""
    from datetime import UTC, datetime

    from chorus.ingestion.postings import from_row

    row = {
        "UUID": "p-1",
        "Text Content": "hi",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Crawled at": "2026-06-01T00:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
    }
    dto = from_row(row, RetentionConfig(default_days=30, enabled=False), datetime(2026, 6, 1, tzinfo=UTC))

    assert dto.retention_until is None


def test_comments_from_row_keeps_row_with_missing_timestamp() -> None:
    """A comment with no parseable Timestamp is kept, not dropped."""
    from chorus.ingestion.comments import from_row

    row = {
        "UUID": "c-1",
        "Text Content": "no timestamp",
        "Timestamp": None,
        "Crawled at": "2026-06-01T09:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
        "Parent Posting UUID": "p-1",
    }
    dto = from_row(row, RetentionConfig(default_days=30))

    assert dto.timestamp is None


def test_comments_from_row_keeps_row_with_missing_crawled_at() -> None:
    """A comment with no Crawled at is kept; crawled_at is informational only."""
    from chorus.ingestion.comments import from_row

    row = {
        "UUID": "c-1",
        "Text Content": "no crawl time",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
        "Parent Posting UUID": "p-1",
    }
    dto = from_row(row, RetentionConfig(default_days=30))

    assert dto.crawled_at is None


def test_comments_from_row_anchors_retention_on_ingested_at() -> None:
    """Comment retention_until is measured from the chorus-set ingested_at."""
    from datetime import UTC, datetime

    from chorus.ingestion.comments import from_row

    ingested = datetime(2026, 6, 1, tzinfo=UTC)
    row = {
        "UUID": "c-1",
        "Text Content": "hi",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Crawled at": "2026-03-01T00:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
        "Parent Posting UUID": "p-1",
    }
    dto = from_row(row, RetentionConfig(default_days=30), ingested)

    assert dto.ingested_at == ingested
    assert dto.retention_until == datetime(2026, 7, 1, tzinfo=UTC)


def test_comments_from_row_retention_disabled_yields_none() -> None:
    """With retention disabled, a comment carries no retention_until deadline."""
    from datetime import UTC, datetime

    from chorus.ingestion.comments import from_row

    row = {
        "UUID": "c-1",
        "Text Content": "hi",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Crawled at": "2026-06-01T00:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
        "Parent Posting UUID": "p-1",
    }
    dto = from_row(row, RetentionConfig(default_days=30, enabled=False), datetime(2026, 6, 1, tzinfo=UTC))

    assert dto.retention_until is None


def test_messages_from_row_keeps_row_with_missing_timestamp() -> None:
    """A message with no Timestamp is kept, not dropped.

    Like postings/comments, a message ``Timestamp`` is optional; retention
    anchors on the chorus-set ``ingested_at``, not the message timestamp, so
    a missing timestamp does not affect the retention clock.
    """
    from chorus.ingestion.messages import from_row

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "a-1",
        "Text": "no timestamp",
        "Timestamp": None,
        "Network": "signal",
    }
    dto = from_row(row, RetentionConfig(default_days=30))

    assert dto.timestamp is None


def test_messages_from_row_anchors_retention_on_ingested_at() -> None:
    """Message retention_until is measured from the chorus-set ingested_at."""
    from datetime import UTC, datetime

    from chorus.ingestion.messages import from_row

    ingested = datetime(2026, 6, 1, tzinfo=UTC)
    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "a-1",
        "Text": "hi",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Network": "signal",
    }
    dto = from_row(row, RetentionConfig(default_days=30), ingested)

    assert dto.ingested_at == ingested
    assert dto.retention_until == datetime(2026, 7, 1, tzinfo=UTC)


def test_messages_from_row_retention_disabled_yields_none() -> None:
    """With retention disabled, a message carries no retention_until deadline."""
    from datetime import UTC, datetime

    from chorus.ingestion.messages import from_row

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "a-1",
        "Text": "hi",
        "Timestamp": "2026-01-01T00:00:00+00:00",
        "Network": "signal",
    }
    dto = from_row(row, RetentionConfig(default_days=30, enabled=False), datetime(2026, 6, 1, tzinfo=UTC))

    assert dto.retention_until is None


def test_comments_from_row_strips_whitespace_padded_timestamps() -> None:
    """Same whitespace-padding fix has to land in the comments parser."""
    from chorus.ingestion.comments import from_row

    row = {
        "UUID": "c-1",
        "Text Content": "great post",
        "Timestamp": "2026-02-13 15:58:07+00   ",
        "Crawled at": "2026-02-14 09:00:00+00:00",
        "Author ID": "a-1",
        "Network": "linkedin",
        "Parent Posting UUID": "p-1",
    }
    dto = from_row(row, RetentionConfig(default_days=30))
    assert dto.uuid == "c-1"
    assert dto.timestamp is not None
    assert dto.timestamp.year == 2026


def test_messages_from_row_strips_whitespace_padded_timestamps() -> None:
    """Same whitespace-padding fix has to land in the messages parser."""
    from chorus.ingestion.messages import from_row

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "a-1",
        "Text": "sync at 3",
        "Timestamp": "2026-02-13 15:58:07+00    ",
        "Network": "signal",
    }
    dto = from_row(row, RetentionConfig(default_days=30))
    assert dto.uuid == "m-1"
    assert dto.timestamp is not None
    assert dto.timestamp.year == 2026


def test_messages_from_row_populates_display_name_from_sender() -> None:
    """``display_name`` for a chat-message sender comes from ``Sender``.

    The upstream messages table carries only a ``Sender`` column (real
    header: ``UUID;Chat ID;Sender;Timestamp;Text;Tags;URL;Chat Group;
    Answers Count;Reply To;Network``) — there is no separate
    display-name column. ``Sender`` is the only human-readable identity
    the table provides, so it must populate ``display_name``. Reading a
    non-existent ``"Sender Display Name"`` column instead left every
    message-sender ``:Author`` with a null ``display_name`` (regression
    surfaced by an ``authors_connected_by_topic`` export, 2026-05-29;
    see ADR 0008).
    """
    from chorus.ingestion.messages import from_row

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "Gunnar Scherf",
        "Text": "hi",
        "Timestamp": "2026-02-13 15:58:07+00",
        "Network": "X",
    }
    dto = from_row(row, RetentionConfig(default_days=30))
    assert dto.sender_display_name == "Gunnar Scherf"
