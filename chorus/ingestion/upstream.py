"""Concrete upstream adapter (stub).

The real implementation will pull from the defined upstream system. For
now this raises so the orchestrator can be wired up against a fake
adapter in tests without an accidental network call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable


class StubUpstreamAdapter:
    """Placeholder concrete adapter — replace with the real pull client."""

    def fetch_postings(self, since: datetime | None) -> Iterable[dict]:
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_comments(self, since: datetime | None) -> Iterable[dict]:
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_messages(self, since: datetime | None) -> Iterable[dict]:
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_profiles(self, since: datetime | None) -> Iterable[dict]:
        raise NotImplementedError("Wire up the real upstream client.")

    def fetch_connections(self, since: datetime | None) -> Iterable[dict]:
        raise NotImplementedError(
            "Connections ingestion is blocked on upstream schema — see ADR 0002."
        )
