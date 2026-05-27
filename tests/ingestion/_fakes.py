"""Shared test fakes for the ingestion pipeline.

Keeping the fake adapter here lets the orchestrator and CLI tests
exercise the same canned rows without one test reaching into another.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class FakeAdapter:
    """In-memory :class:`UpstreamAdapter` yielding one canned row per stage.

    Used by the orchestrator and CLI tests to drive ``run_once``
    end-to-end without a real upstream source.
    """

    def __init__(self) -> None:
        """Configure the fake. No options needed in the v1 contract."""

    def fetch_postings(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned posting row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row populated with the upstream column names the
            postings DTO maps from.
        """
        yield {
            "UUID": "p-1",
            "Posting ID": "post-net-1",
            "Text Content": "hello berlin",
            "Timestamp": "2026-05-01T10:00:00+00:00",
            "Crawled at": "2026-05-02T10:00:00+00:00",
            "Author ID": "a-1",
            "Author": "Alice",
            "Network": "linkedin",
            "Tags": "news, politics",
        }

    def fetch_comments(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned comment row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row referencing the canned posting via
            ``Parent Posting UUID``.
        """
        yield {
            "UUID": "c-1",
            "Comment ID": "comment-net-1",
            "Text Content": "great post",
            "Timestamp": "2026-05-01T11:00:00+00:00",
            "Crawled at": "2026-05-02T11:00:00+00:00",
            "Author ID": "a-2",
            "Network": "linkedin",
            # Upstream references the parent posting by its own Posting ID
            # (the postings table's primary key), not by chorus UUID. The
            # orchestrator resolves this to ``Parent Posting UUID`` before
            # the comment row reaches ``from_row``.
            "Posting ID": "post-net-1",
        }

    def fetch_messages(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned chat-message row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row populated with the upstream column names the
            messages DTO maps from.
        """
        yield {
            "UUID": "m-1",
            "Chat ID": "chat-1",
            "Sender": "a-1",
            "Text": "sync at 3",
            "Timestamp": "2026-05-01T12:00:00+00:00",
            "Network": "signal",
        }

    def fetch_profiles(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned author-profile row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row referencing the canned posting author by ``ID``.
        """
        yield {
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

    def fetch_connections(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned connection row.

        The row places the canned posting author (``a-1``) as a
        follower of a new target user (``a-2``), exercising the
        :FOLLOWS edge writer plus the thin :Author upsert for the
        previously-unknown target.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row populated with the upstream column names the
            connections DTO maps from.
        """
        yield {
            "Network Object ID": "a-1",
            "Network Object ID selected conn. User": "a-2",
            "Vanity Name": "alice",
            "Name": "Alice Anderson",
            "Vanity Name selected conn. User": "bob",
            "Friend": "No",
            "Follower": "Yes",
            "Following": "No",
            "Crawled at": "2026-05-26T02:31:43+00:00",
            "Network": "linkedin",
            "Profile Type": "user",
            "Url": "https://example.test/alice",
        }
