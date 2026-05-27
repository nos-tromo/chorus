"""Posting (top-level post) DTO and graph write.

Maps the upstream `postings` table to `(:Post:Posting)` plus the
surrounding entity/group/platform/attachment nodes. The Cypher MERGEs
authors, platform, and (optional) group before linking the post.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.utils.env_cfg import RetentionConfig


class PostingDTO(BaseModel):
    """Validated DTO for one upstream posting row.

    Field naming follows snake_case after mapping from the upstream
    table headers (see CLAUDE.md §Upstream data format). UUID is the
    primary key; network-side ids are kept as properties for traceability.

    Attributes:
        uuid: Canonical chorus identifier.
        network_posting_id: Upstream-side post id, kept for traceability.
        url: Original post URL.
        text: Body of the post.
        timestamp: Content creation time (drives retention).
        timezone_name: Source-supplied timezone label.
        crawled_at: Ingestion-side timestamp.
        last_updated: Content edit time (if known).
        location: Source-supplied location string.
        task: Upstream task label.
        author_id: Network author id, joins to ``:Author.id``.
        author_display_name: Human-readable author name.
        vanity_name: Platform-specific slug (e.g. LinkedIn vanity).
        co_author_id: Network id of a co-author, if any.
        quoted_user_id: Network id of a quoted user (user reference only;
            the graph cannot represent a quoted *post*).
        expected_reactions: Reaction count the upstream expected to collect.
        collected_reactions: Reaction count actually collected.
        expected_comments: Comment count the upstream expected to collect.
        collected_comments: Comment count actually collected.
        network: Platform name (resolves to ``:Platform``).
        posted_in_group: Group id when the post was made inside a group.
        filename: Multimedia attachment filename, if present.
        system_tags: Upstream ``Tags`` field as a string list.
        retention_until: Absolute time the nightly sweeper should hard-delete
            this post.
    """

    uuid: str
    network_posting_id: str | None = None
    url: str | None = None
    text: str
    timestamp: datetime
    timezone_name: str | None = None
    crawled_at: datetime
    last_updated: datetime | None = None
    location: str | None = None
    task: str | None = None
    author_id: str
    author_display_name: str | None = None
    vanity_name: str | None = None
    co_author_id: str | None = None
    quoted_user_id: str | None = None
    expected_reactions: int | None = None
    collected_reactions: int | None = None
    expected_comments: int | None = None
    collected_comments: int | None = None
    network: str
    posted_in_group: str | None = None
    filename: str | None = None
    system_tags: list[str] = Field(default_factory=list)
    retention_until: datetime


def from_row(row: dict[str, Any], retention: RetentionConfig) -> PostingDTO:
    """Adapt one upstream posting row to a :class:`PostingDTO`.

    Field-name mapping mirrors the upstream table headers documented in
    CLAUDE.md §Upstream data format. ``retention_until`` is derived from
    the post's ``Timestamp`` plus the configured default retention
    window — not from ``Crawled at``.

    Args:
        row: One raw row as returned by the upstream adapter.
        retention: Retention configuration, used to compute
            ``retention_until``.

    Returns:
        A populated, validated :class:`PostingDTO`.

    Raises:
        KeyError: If a required upstream column is missing.
        ValueError: If a timestamp column is malformed.
        pydantic.ValidationError: If the resulting DTO fails validation.
    """
    ts = _coerce_dt(row["Timestamp"])
    return PostingDTO(
        uuid=row["UUID"],
        network_posting_id=row.get("Network Posting ID") or row.get("Posting ID"),
        url=row.get("URL"),
        text=row.get("Text Content") or "",
        timestamp=ts,
        timezone_name=row.get("Timezone"),
        crawled_at=_coerce_dt(row["Crawled at"]),
        last_updated=_coerce_dt_opt(row.get("Date last updated")),
        location=row.get("Location"),
        task=row.get("Task"),
        author_id=str(row["Author ID"]),
        author_display_name=row.get("Author"),
        vanity_name=row.get("Vanity Name"),
        co_author_id=_str_or_none(row.get("Co-Author")),
        quoted_user_id=_str_or_none(row.get("Quoted User")),
        expected_reactions=_int_or_none(row.get("Expected Reactions")),
        collected_reactions=_int_or_none(row.get("Collected Reactions")),
        expected_comments=_int_or_none(row.get("Expected Comments")),
        collected_comments=_int_or_none(row.get("Collected Comments")),
        network=row["Network"],
        posted_in_group=_str_or_none(row.get("Posted in Group")),
        filename=_str_or_none(row.get("Filename")),
        system_tags=_tags(row.get("Tags")),
        retention_until=ts + timedelta(days=retention.default_days),
    )


def write(driver: Driver, dto: PostingDTO) -> None:
    """Write one :class:`PostingDTO` to the graph idempotently.

    MERGEs author, platform, optional group and attachment nodes, then
    links them to the ``:Post:Posting`` node. Co-authors and quoted
    users are MERGEd as additional ``:Author`` nodes. Safe to call
    repeatedly with the same DTO.

    Args:
        driver: Open Neo4j driver.
        dto: Validated posting DTO to write.
    """
    cypher = """
    MERGE (a:Author {id: $author_id})
      ON CREATE SET a.handle = $vanity_name, a.display_name = $author_display_name,
                    a.platform = $network
    MERGE (pl:Platform {name: $network})
    MERGE (p:Post:Posting {uuid: $uuid})
      ON CREATE SET
        p.network_post_id     = $network_posting_id,
        p.url                 = $url,
        p.text                = $text,
        p.timestamp           = datetime($timestamp),
        p.timezone            = $timezone_name,
        p.crawled_at          = datetime($crawled_at),
        p.last_updated        = CASE WHEN $last_updated IS NULL THEN NULL
                                     ELSE datetime($last_updated) END,
        p.location            = $location,
        p.task                = $task,
        p.expected_reactions  = $expected_reactions,
        p.collected_reactions = $collected_reactions,
        p.expected_comments   = $expected_comments,
        p.collected_comments  = $collected_comments,
        p.system_tags         = $system_tags,
        p.retention_until     = datetime($retention_until)
    MERGE (a)-[:AUTHORED]->(p)
    MERGE (p)-[:ON_PLATFORM]->(pl)
    WITH p
    FOREACH (gid IN CASE WHEN $posted_in_group IS NULL THEN [] ELSE [$posted_in_group] END |
      MERGE (g:Group {id: gid})
      MERGE (p)-[:IN_GROUP]->(g)
    )
    FOREACH (fname IN CASE WHEN $filename IS NULL THEN [] ELSE [$filename] END |
      MERGE (att:Attachment {filename: fname})
      MERGE (p)-[:HAS_ATTACHMENT]->(att)
    )
    FOREACH (co IN CASE WHEN $co_author_id IS NULL THEN [] ELSE [$co_author_id] END |
      MERGE (cau:Author {id: co})
      MERGE (cau)-[:CO_AUTHORED]->(p)
    )
    FOREACH (qu IN CASE WHEN $quoted_user_id IS NULL THEN [] ELSE [$quoted_user_id] END |
      MERGE (qau:Author {id: qu})
      MERGE (qau)-[:QUOTED_IN]->(p)
    )
    """
    params = dto.model_dump(mode="json")
    with driver.session() as s:
        s.run(cypher, **params)


def _coerce_dt(value: Any) -> datetime:
    """Coerce ``value`` into an aware UTC :class:`datetime`.

    Naive datetimes are assumed to be UTC. Strings are parsed via
    :meth:`datetime.fromisoformat`.

    Args:
        value: A ``datetime``, ISO-8601 string, or anything stringifiable.

    Returns:
        A timezone-aware :class:`datetime`.

    Raises:
        ValueError: If ``value`` is a string that does not parse as ISO-8601.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).strip())


def _coerce_dt_opt(value: Any) -> datetime | None:
    """Coerce ``value`` to a datetime, or ``None`` when missing.

    Args:
        value: Candidate value (``None``, empty string, datetime, or
            ISO-8601 string).

    Returns:
        ``None`` for missing/empty inputs; otherwise the result of
        :func:`_coerce_dt`.
    """
    if value is None or value == "":
        return None
    return _coerce_dt(value)


def _str_or_none(value: Any) -> str | None:
    """Coerce ``value`` to a string, or ``None`` when missing.

    Args:
        value: Candidate value.

    Returns:
        ``None`` for ``None``/empty inputs; ``str(value)`` otherwise.
    """
    if value is None or value == "":
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    """Coerce ``value`` to an int, or ``None`` when missing.

    Args:
        value: Candidate value.

    Returns:
        ``None`` for ``None``/empty inputs; ``int(value)`` otherwise.

    Raises:
        ValueError: If ``value`` is non-empty and not int-parseable.
    """
    if value is None or value == "":
        return None
    return int(value)


def _tags(value: Any) -> list[str]:
    """Parse the upstream ``Tags`` column into a list of tag strings.

    Accepts either a pre-split list (used by the test fixtures) or a
    comma-separated string (the upstream's native format). Whitespace
    is trimmed and empty entries are dropped.

    Args:
        value: Either a list of stringifiable items, a comma-separated
            string, or ``None``/empty for "no tags".

    Returns:
        Tag strings in source order, with empties removed.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [t.strip() for t in str(value).split(",") if t.strip()]
