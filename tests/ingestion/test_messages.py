"""messages ingestion: handle derivation, row parsing, and :Author write.

The chat-messages table carries no handle column, but for X/Twitter the
author handle is latent in the message URL
(``https://x.com/<handle>/status/<id>``). These tests pin the
conservative extraction (ADR 0008 step 2) and its propagation to the
``:Author`` node.
"""

from __future__ import annotations

from neo4j import Driver

from chorus.utils.env_cfg import RetentionConfig


def test_handle_from_url_extracts_x_status_handle() -> None:
    """The handle is the first path segment of an X status URL.

    Cases drawn verbatim from a real messages export.
    """
    from chorus.ingestion.messages import _handle_from_url

    assert _handle_from_url("https://x.com/gunnarscherf/status/1954777707692122179") == "gunnarscherf"
    # case is preserved — X handles are case-insensitive but displayed as set
    assert _handle_from_url("https://x.com/DeliaKlagesAfD/status/1970462465755619760") == "DeliaKlagesAfD"
    # twitter.com is the same platform
    assert _handle_from_url("https://twitter.com/LandHarburg/status/1605304997214048257") == "LandHarburg"


def test_handle_from_url_returns_none_when_not_derivable() -> None:
    """Extraction is conservative: a wrong handle is worse than none.

    Anything that is not a known X/Twitter status URL yields ``None``
    rather than a guessed segment.
    """
    from chorus.ingestion.messages import _handle_from_url

    assert _handle_from_url(None) is None
    assert _handle_from_url("") is None
    # no /status/ segment — a bare profile or home URL
    assert _handle_from_url("https://x.com/gunnarscherf") is None
    assert _handle_from_url("https://x.com/home") is None
    # reserved non-handle first segment (x.com/i/web/status/... style)
    assert _handle_from_url("https://x.com/i/status/123") is None
    # a different host whose URL grammar we have not confirmed
    assert _handle_from_url("https://t.me/somechannel/42") is None
    assert _handle_from_url("https://example.test/foo/status/1") is None


def test_from_row_sets_handle_from_url() -> None:
    """``from_row`` derives ``handle`` from the message URL when possible."""
    from chorus.ingestion.messages import from_row

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "Gunnar Scherf",
        "Text": "hi",
        "Timestamp": "2026-02-13 15:58:07+00",
        "URL": "https://x.com/gunnarscherf/status/1954777707692122179",
        "Network": "X",
    }
    dto = from_row(row, RetentionConfig(default_days=30))
    assert dto.handle == "gunnarscherf"


def test_from_row_handle_is_none_without_derivable_url() -> None:
    """No URL (e.g. a Signal chat) leaves ``handle`` unset."""
    from chorus.ingestion.messages import from_row

    row = {
        "UUID": "m-2",
        "Chat ID": "chat-1",
        "Sender": "a-1",
        "Text": "sync at 3",
        "Timestamp": "2026-02-13 15:58:07+00",
        "Network": "signal",
    }
    dto = from_row(row, RetentionConfig(default_days=30))
    assert dto.handle is None


def test_write_sets_author_handle(migrated_driver: Driver) -> None:
    """``write`` records the derived handle on the sender ``:Author``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.messages import from_row, write

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "Gunnar Scherf",
        "Text": "hi",
        "Timestamp": "2026-02-13 15:58:07+00",
        "URL": "https://x.com/gunnarscherf/status/1954777707692122179",
        "Network": "X",
    }
    write(migrated_driver, from_row(row, RetentionConfig(default_days=30)))

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: 'Gunnar Scherf'}) RETURN a").single()["a"]  # type: ignore[index]
        assert a["handle"] == "gunnarscherf"
        assert a["display_name"] == "Gunnar Scherf"


def test_write_backfills_handle_for_url_less_first_message(migrated_driver: Driver) -> None:
    """A later message carrying a URL backfills a handle left null earlier.

    Senders span many messages; the first one seen may be a URL-less
    reply. The handle must not be permanently lost just because the
    node was created without it.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.messages import from_row, write

    base = {
        "Chat ID": "chat-1",
        "Sender": "Gunnar Scherf",
        "Text": "hi",
        "Timestamp": "2026-02-13 15:58:07+00",
        "Network": "X",
    }
    # first message: no URL -> node created with a null handle
    write(migrated_driver, from_row({**base, "UUID": "m-1"}, RetentionConfig(default_days=30)))
    # second message: carries the status URL -> handle backfilled
    write(
        migrated_driver,
        from_row(
            {**base, "UUID": "m-2", "URL": "https://x.com/gunnarscherf/status/1954777707692122179"},
            RetentionConfig(default_days=30),
        ),
    )

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: 'Gunnar Scherf'}) RETURN a").single()["a"]  # type: ignore[index]
        assert a["handle"] == "gunnarscherf"
        count = s.run("MATCH (a:Author) RETURN count(a) AS c").single()["c"]  # type: ignore[index]
        assert count == 1


def test_write_backfills_display_name_left_null_by_prefix_ingest(migrated_driver: Driver) -> None:
    """Re-ingest repairs a sender node the pre-fix code left without a name.

    Before the display_name fix, message senders were created with a
    null ``display_name`` (the parser read a non-existent column). A node
    already in that state must be repaired on the next ingest, not left
    broken until a full wipe-and-reload — so identity fields backfill on
    MATCH as well as CREATE.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.messages import from_row, write

    # the residue of a pre-fix ingest: id + platform set, display_name null
    with migrated_driver.session() as s:
        s.run("MERGE (a:Author {id: 'Gunnar Scherf'}) ON CREATE SET a.platform = 'X'")

    row = {
        "UUID": "m-1",
        "Chat ID": "chat-1",
        "Sender": "Gunnar Scherf",
        "Text": "hi",
        "Timestamp": "2026-02-13 15:58:07+00",
        "URL": "https://x.com/gunnarscherf/status/1954777707692122179",
        "Network": "X",
    }
    write(migrated_driver, from_row(row, RetentionConfig(default_days=30)))

    with migrated_driver.session() as s:
        a = s.run("MATCH (a:Author {id: 'Gunnar Scherf'}) RETURN a").single()["a"]  # type: ignore[index]
        assert a["display_name"] == "Gunnar Scherf"
        assert a["handle"] == "gunnarscherf"
