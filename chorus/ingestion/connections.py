"""Connections (social-graph edge) ingestion.

This module handles the upstream's node-edge-node *edge* table — the
social graph, one connected user per row from the perspective of a
target "selected conn. User". The upstream also emits a profile-per-row
table under the same "connections" umbrella; that table is author-
profile enrichment and is handled separately by `profiles.py` (see
ADR 0006). Do not conflate the two.

Each row carries three Yes/No relationship flags — ``Friend``,
``Follower``, ``Following`` — which can coexist. ``Follower=Yes`` means
the row user follows the target; ``Following=Yes`` means the target
follows the row user. ``Friend=Yes`` is symmetric. Engagement metrics
(``Posting Conn.``, ``React. *``, ``ChatMessage Conn.``, ``Media Conn.``)
are deliberately not mapped to the graph in v1 — the raw store retains
them; derivable from postings/comments/reactions if needed. See
ADR 0007 for the full design rationale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from neo4j import Driver
from pydantic import BaseModel

from chorus.ingestion.profiles import _coerce_dt_opt, _str_or_none


class ConnectionDTO(BaseModel):
    """Validated DTO for one upstream ``connections`` row.

    Encodes one row's worth of (row_user, target) edge information. The
    flags decide which graph edges the writer emits; a row with all
    flags ``False`` is filtered out at :func:`from_row`.

    Attributes:
        row_user_id: Network author id of the connected user (the row).
        row_user_handle: Vanity name / handle of the connected user.
        row_user_display_name: Human-readable name.
        row_user_url: Profile URL.
        row_user_platform: Platform name (``Network`` column).
        row_user_profile_type: Upstream profile-type label.
        target_id: Network author id of the constant target user.
        target_handle: Vanity name of the target user.
        is_friend: ``Friend == "Yes"`` — symmetric :FRIENDS_WITH edge.
        is_follower: ``Follower == "Yes"`` — row user follows target.
        is_following: ``Following == "Yes"`` — target follows row user.
        crawled_at: Crawl timestamp recorded on each emitted edge.
    """

    row_user_id: str
    row_user_handle: str | None = None
    row_user_display_name: str | None = None
    row_user_url: str | None = None
    row_user_platform: str | None = None
    row_user_profile_type: str | None = None

    target_id: str
    target_handle: str | None = None

    is_friend: bool
    is_follower: bool
    is_following: bool

    crawled_at: datetime | None = None


def from_row(row: dict[str, Any]) -> ConnectionDTO | None:
    """Adapt one upstream ``connections`` row to a :class:`ConnectionDTO`.

    Drops rows that carry no edge signal — self-loops where row and
    target share an id, and rows where all three flags are ``No`` —
    rather than producing a DTO the writer would have to filter out
    again downstream.

    Args:
        row: One raw row as returned by the upstream adapter.

    Returns:
        A populated :class:`ConnectionDTO`, or ``None`` if the row was
        a self-loop or carried no edge flag.

    Raises:
        KeyError: If a required identity column (``Network Object ID``,
            ``Network Object ID selected conn. User``) is missing.
    """
    row_user_id = str(row["Network Object ID"]).strip()
    target_id = str(row["Network Object ID selected conn. User"]).strip()

    if row_user_id == target_id:
        logger.warning("connections row dropped: self-loop on id={!r}", row_user_id)
        return None

    is_friend = _yes(row.get("Friend"))
    is_follower = _yes(row.get("Follower"))
    is_following = _yes(row.get("Following"))
    if not (is_friend or is_follower or is_following):
        logger.info(
            "connections row dropped: no flag set (row_user={!r}, target={!r})",
            row_user_id,
            target_id,
        )
        return None

    return ConnectionDTO(
        row_user_id=row_user_id,
        row_user_handle=_str_or_none(row.get("Vanity Name")),
        row_user_display_name=_str_or_none(row.get("Name")),
        row_user_url=_str_or_none(row.get("Url")),
        row_user_platform=_str_or_none(row.get("Network")),
        row_user_profile_type=_str_or_none(row.get("Profile Type")),
        target_id=target_id,
        target_handle=_str_or_none(row.get("Vanity Name selected conn. User")),
        is_friend=is_friend,
        is_follower=is_follower,
        is_following=is_following,
        crawled_at=_coerce_dt_opt(row.get("Crawled at")),
    )


def write_batch(driver: Driver, dtos: list[ConnectionDTO]) -> dict[str, int]:
    """Write a batch of connection DTOs as :Author nodes and edges.

    Three UNWIND phases per call, all in one session:

    1. Upsert every endpoint :Author with ``ON CREATE SET`` (profiles
       remain authoritative for identity per ADR 0006).
    2. MERGE :FOLLOWS edges in both directions emitted by the batch
       (row→target for ``is_follower``, target→row for ``is_following``).
       ``crawled_at`` is updated on re-encounter (latest wins).
    3. MERGE :FRIENDS_WITH edges in canonical direction (lower id →
       higher id, lexicographic) so re-emission from either side
       dedupes by MERGE.

    Args:
        driver: Open Neo4j driver.
        dtos: Validated connection DTOs to write. Empty input is a
            no-op.

    Returns:
        Counts of items processed: ``{"authors": N, "follows": N,
        "friends_with": N}``. ``authors`` is the number of unique
        endpoints upserted (some may already have existed); ``follows``
        and ``friends_with`` count edges visited by the MERGE.
    """
    if not dtos:
        return {"authors": 0, "follows": 0, "friends_with": 0}

    authors: dict[str, dict[str, Any]] = {}
    follows: list[dict[str, Any]] = []
    friends: list[dict[str, Any]] = []

    for dto in dtos:
        authors.setdefault(
            dto.row_user_id,
            {
                "id": dto.row_user_id,
                "handle": dto.row_user_handle,
                "display_name": dto.row_user_display_name,
                "url": dto.row_user_url,
                "platform": dto.row_user_platform,
                "profile_type": dto.row_user_profile_type,
            },
        )
        # The target is only ever identified by id + handle inside the
        # connections file; richer identity comes from profiles.csv.
        authors.setdefault(
            dto.target_id,
            {
                "id": dto.target_id,
                "handle": dto.target_handle,
                "display_name": None,
                "url": None,
                "platform": dto.row_user_platform,
                "profile_type": None,
            },
        )
        crawled_at_iso = dto.crawled_at.isoformat() if dto.crawled_at else None
        if dto.is_follower:
            follows.append({"from_id": dto.row_user_id, "to_id": dto.target_id, "crawled_at": crawled_at_iso})
        if dto.is_following:
            follows.append({"from_id": dto.target_id, "to_id": dto.row_user_id, "crawled_at": crawled_at_iso})
        if dto.is_friend:
            lo, hi = sorted((dto.row_user_id, dto.target_id))
            friends.append({"from_id": lo, "to_id": hi, "crawled_at": crawled_at_iso})

    counts = {"authors": len(authors), "follows": 0, "friends_with": 0}
    with driver.session() as s:
        s.run(_AUTHORS_CYPHER, authors=list(authors.values()))
        if follows:
            rec = s.run(_FOLLOWS_CYPHER, follows=follows).single()
            if rec is not None:
                counts["follows"] = int(rec["n"])
        if friends:
            rec = s.run(_FRIENDS_CYPHER, friends=friends).single()
            if rec is not None:
                counts["friends_with"] = int(rec["n"])
    return counts


def _yes(value: Any) -> bool:
    """Coerce the upstream Yes/No flag to a bool.

    Anything other than the literal string ``"Yes"`` (case-insensitive,
    whitespace-stripped) is treated as ``False``. Missing and empty
    values are ``False``.

    Args:
        value: Raw cell value.

    Returns:
        ``True`` for ``"Yes"``; ``False`` otherwise.
    """
    if value is None:
        return False
    return str(value).strip().lower() == "yes"


_AUTHORS_CYPHER = """
UNWIND $authors AS a
MERGE (n:Author {id: a.id})
  ON CREATE SET
    n.handle       = a.handle,
    n.vanity_name  = a.handle,
    n.display_name = a.display_name,
    n.url          = a.url,
    n.platform     = a.platform,
    n.profile_type = a.profile_type
"""

_FOLLOWS_CYPHER = """
UNWIND $follows AS e
MATCH (src:Author {id: e.from_id})
MATCH (dst:Author {id: e.to_id})
MERGE (src)-[r:FOLLOWS]->(dst)
SET r.crawled_at = CASE WHEN e.crawled_at IS NULL THEN r.crawled_at
                        ELSE datetime(e.crawled_at) END
RETURN count(r) AS n
"""

_FRIENDS_CYPHER = """
UNWIND $friends AS e
MATCH (a:Author {id: e.from_id})
MATCH (b:Author {id: e.to_id})
MERGE (a)-[r:FRIENDS_WITH]->(b)
SET r.crawled_at = CASE WHEN e.crawled_at IS NULL THEN r.crawled_at
                        ELSE datetime(e.crawled_at) END
RETURN count(r) AS n
"""
