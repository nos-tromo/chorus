"""Upstream adapter Protocol.

There is exactly one upstream system; this Protocol exists so concrete
adapters can be swapped in tests and so the orchestrator depends on a
type rather than a concrete class. If a *second* upstream ever materializes,
write a second adapter — do not generalize this one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol


class UpstreamAdapter(Protocol):
    """Structural interface for pulling rows from the upstream system.

    Each method yields raw upstream rows (the upstream's native column
    names, untouched) for one of the five table kinds chorus consumes.
    Conversion to DTOs and graph writes happen downstream in
    ``ingestion/<kind>.py``.

    Implementations should yield batches lazily so the orchestrator can
    stream rows into the raw store without buffering the full table.
    """

    def fetch_postings(self, since: datetime | None) -> Iterable[dict]:
        """Yield posting rows from the upstream system.

        Args:
            since: If provided, only yield rows with a crawled-at or
                last-updated timestamp greater than this. ``None`` means
                full backfill.

        Returns:
            An iterable of raw upstream posting rows.
        """
        ...

    def fetch_comments(self, since: datetime | None) -> Iterable[dict]:
        """Yield comment rows from the upstream system.

        Args:
            since: If provided, only yield rows newer than this.
                ``None`` means full backfill.

        Returns:
            An iterable of raw upstream comment rows.
        """
        ...

    def fetch_messages(self, since: datetime | None) -> Iterable[dict]:
        """Yield chat-message rows from the upstream system.

        Args:
            since: If provided, only yield rows newer than this.
                ``None`` means full backfill.

        Returns:
            An iterable of raw upstream message rows.
        """
        ...

    def fetch_profiles(self, since: datetime | None) -> Iterable[dict]:
        """Yield author-profile rows from the upstream system.

        Args:
            since: If provided, only yield rows newer than this.
                ``None`` means full backfill.

        Returns:
            An iterable of raw upstream profile rows.
        """
        ...

    def fetch_connections(self, since: datetime | None) -> Iterable[dict]:
        """Yield social-graph edge rows from the upstream system.

        Args:
            since: If provided, only yield rows newer than this.
                ``None`` means full backfill.

        Returns:
            An iterable of raw upstream connection rows.
        """
        ...
