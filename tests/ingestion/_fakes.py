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

    def __init__(self, *, connections_raises: bool = True) -> None:
        """Configure the fake.

        Args:
            connections_raises: When ``True`` (the default), the
                connections fetcher raises ``NotImplementedError`` to
                exercise the orchestrator's skip path. ``False`` yields
                an empty iterator so the "rows present but stubbed"
                branch is covered instead.
        """
        self._connections_raises = connections_raises

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
            "Text Content": "great post",
            "Timestamp": "2026-05-01T11:00:00+00:00",
            "Crawled at": "2026-05-02T11:00:00+00:00",
            "Author ID": "a-2",
            "Network": "linkedin",
            "Parent Posting UUID": "p-1",
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
        """Return an empty iterator or raise, per the constructor toggle.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Returns:
            An empty iterator when ``connections_raises`` is ``False``.

        Raises:
            NotImplementedError: When ``connections_raises`` is ``True``
                (the default), to exercise the orchestrator's skip path.
        """
        if self._connections_raises:
            raise NotImplementedError("schema pending")
        return iter(())
