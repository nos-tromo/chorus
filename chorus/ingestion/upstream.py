"""Concrete upstream adapter (stub).

The real implementation will pull from the defined upstream system. For
now this raises so the orchestrator can be wired up against a fake
adapter in tests without an accidental network call.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any


class StubUpstreamAdapter:
    """Placeholder concrete adapter — replace with the real pull client.

    Conforms structurally to :class:`chorus.ingestion.adapter.UpstreamAdapter`.
    Every method raises so accidental wiring to this stub in production
    fails loudly instead of silently no-oping.
    """

    def fetch_postings(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Raise — stub does not pull postings.

        Args:
            since: Ignored; the real adapter will use this for delta pulls.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_comments(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Raise — stub does not pull comments.

        Args:
            since: Ignored; the real adapter will use this for delta pulls.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_messages(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Raise — stub does not pull chat messages.

        Args:
            since: Ignored; the real adapter will use this for delta pulls.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_profiles(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Raise — stub does not pull author profiles.

        Args:
            since: Ignored; the real adapter will use this for delta pulls.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_connections(self, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Raise — connections ingestion blocked on upstream schema.

        Args:
            since: Ignored; the real adapter will use this for delta pulls.

        Raises:
            NotImplementedError: Always. The connections schema is still
                pending; see ADR 0002.
        """
        raise NotImplementedError("Connections ingestion is blocked on upstream schema — see ADR 0002.")
