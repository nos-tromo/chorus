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
    def fetch_postings(self, since: datetime | None) -> Iterable[dict]: ...
    def fetch_comments(self, since: datetime | None) -> Iterable[dict]: ...
    def fetch_messages(self, since: datetime | None) -> Iterable[dict]: ...
    def fetch_profiles(self, since: datetime | None) -> Iterable[dict]: ...
    def fetch_connections(self, since: datetime | None) -> Iterable[dict]: ...
