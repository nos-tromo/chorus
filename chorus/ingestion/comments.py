"""Comment (reply to a posting or another comment) DTO + graph write.

`Posting Text` and `Parent Comment Text` are useful for extraction context
during ingestion but are *not* stored on the comment node — the parent's
text is already on the parent node, and duplicating it would inflate
storage and risk drift.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.utils.env_cfg import RetentionConfig


class CommentDTO(BaseModel):
    """Validated DTO for one upstream comment row.

    Comments are keyed by UUID. Parent references use the parent's
    UUID, not the upstream-side comment/post id — the caller is
    responsible for resolving those before building the DTO.

    Attributes:
        uuid: Canonical chorus identifier.
        network_object_id: Upstream-side network object id.
        network_comment_id: Upstream-side comment id.
        url: Original comment URL.
        text: Body of the comment.
        timestamp: Content creation time; optional (the upstream omits it
            for some rows). Informational only — does not drive retention.
        crawled_at: Upstream crawl time; optional. Informational only — the
            upstream crawler owns its own retention clock.
        author_id: Network author id, joins to ``:Author.id``.
        author_display_name: Human-readable author name.
        vanity_name: Platform-specific slug for the author.
        replies_count: Reply count reported by the upstream.
        reactions_count: Reaction count reported by the upstream.
        parent_comment_uuid: UUID of the parent comment when this is a
            threaded reply; ``None`` for top-level comments.
        parent_posting_uuid: UUID of the posting this comment is attached
            to. Required.
        network: Platform name (resolves to ``:Platform``).
        system_tags: Upstream ``Tags`` field as a string list.
        ingested_at: Time chorus ingested this row (chorus-set, not from the
            upstream). The anchor retention is measured from.
        retention_until: Absolute time the nightly sweeper should
            hard-delete this comment (``ingested_at`` + the configured
            window); ``None`` when retention is disabled, leaving the comment
            non-expiring.
    """

    uuid: str
    network_object_id: str | None = None
    network_comment_id: str | None = None
    url: str | None = None
    text: str
    timestamp: datetime | None = None
    crawled_at: datetime | None = None
    author_id: str
    author_display_name: str | None = None
    vanity_name: str | None = None
    replies_count: int | None = None
    reactions_count: int | None = None
    parent_comment_uuid: str | None = None
    parent_posting_uuid: str
    network: str
    system_tags: list[str] = Field(default_factory=list)
    ingested_at: datetime
    retention_until: datetime | None = None


def from_row(row: dict[str, Any], retention: RetentionConfig, ingested_at: datetime | None = None) -> CommentDTO:
    """Adapt one upstream comment row to a :class:`CommentDTO`.

    The caller must resolve parent posting and parent comment UUIDs
    upstream (the raw rows carry the upstream's network ids, not chorus
    UUIDs) and supply them as ``Parent Posting UUID`` / ``Parent
    Comment UUID`` keys. ``timestamp`` and ``crawled_at`` are optional and
    informational; ``retention_until`` anchors on ``ingested_at``.

    Args:
        row: One raw row as returned by the upstream adapter, augmented
            with the resolved parent UUIDs.
        retention: Retention configuration, used to compute
            ``retention_until``.
        ingested_at: Ingestion time to stamp and anchor retention on;
            defaults to ``datetime.now(UTC)``. The orchestrator passes one
            value per run so a whole run shares a consistent clock.

    Returns:
        A populated, validated :class:`CommentDTO`.

    Raises:
        KeyError: If a required upstream column (``UUID``, ``Author ID``,
            ``Network``, ``Parent Posting UUID``) is missing.
        ValueError: If a present ``Timestamp`` / ``Crawled at`` value is
            malformed.
        pydantic.ValidationError: If the resulting DTO fails validation.
    """
    ingested_at = ingested_at or datetime.now(UTC)
    crawled_at = _coerce_dt_opt(row.get("Crawled at"))
    # Note: Posting ID / Parent Comment ID upstream are the upstream's
    # network IDs. Chorus keys on UUID, so the caller must supply the
    # resolved parent UUIDs (typically by upstream UUID lookup before
    # building the DTO).
    return CommentDTO(
        uuid=row["UUID"],
        network_object_id=row.get("Network Object ID"),
        network_comment_id=row.get("Comment ID"),
        url=row.get("URL"),
        text=row.get("Text Content") or "",
        timestamp=_coerce_dt_opt(row.get("Timestamp")),
        crawled_at=crawled_at,
        author_id=str(row["Author ID"]),
        author_display_name=row.get("Author"),
        vanity_name=row.get("Vanity Name"),
        replies_count=_int_or_none(row.get("Replies Count")),
        reactions_count=_int_or_none(row.get("Reactions Count")),
        parent_comment_uuid=row.get("Parent Comment UUID"),
        parent_posting_uuid=row["Parent Posting UUID"],
        network=row["Network"],
        system_tags=_tags(row.get("Tags")),
        ingested_at=ingested_at,
        retention_until=retention.until(ingested_at),
    )


def write(driver: Driver, dto: CommentDTO) -> None:
    """Write one :class:`CommentDTO` to the graph idempotently.

    MERGEs author, platform, and the parent posting (creating a thin
    ``:Post:Posting`` stub if it doesn't already exist), then links the
    comment via ``[:AUTHORED]``, ``[:ON_PLATFORM]``, ``[:ON]``, and
    optionally ``[:REPLIES_TO]``.

    Args:
        driver: Open Neo4j driver.
        dto: Validated comment DTO to write.
    """
    cypher = """
    MERGE (a:Author {id: $author_id})
      ON CREATE SET a.handle = $vanity_name, a.display_name = $author_display_name,
                    a.platform = $network
    MERGE (pl:Platform {name: $network})
    MERGE (parent:Post:Posting {uuid: $parent_posting_uuid})
    MERGE (c:Post:Comment {uuid: $uuid})
      ON CREATE SET
        c.network_object_id = $network_object_id,
        c.url               = $url,
        c.text              = $text,
        c.timestamp         = datetime($timestamp),
        c.crawled_at        = datetime($crawled_at),
        c.replies_count     = $replies_count,
        c.reactions_count   = $reactions_count,
        c.system_tags       = $system_tags,
        c.ingested_at       = datetime($ingested_at),
        c.retention_until   = datetime($retention_until)
    MERGE (a)-[:AUTHORED]->(c)
    MERGE (c)-[:ON_PLATFORM]->(pl)
    MERGE (c)-[:ON]->(parent)
    WITH c
    FOREACH (pc IN CASE WHEN $parent_comment_uuid IS NULL THEN []
                        ELSE [$parent_comment_uuid] END |
      MERGE (pcn:Post:Comment {uuid: pc})
      MERGE (c)-[:REPLIES_TO]->(pcn)
    )
    """
    with driver.session() as s:
        s.run(cypher, **dto.model_dump(mode="json"))


def _coerce_dt(value: Any) -> datetime:
    """Coerce ``value`` into an aware UTC :class:`datetime`.

    Naive datetimes are assumed to be UTC; strings are parsed via
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

    Accepts either a pre-split list or a comma-separated string;
    whitespace is trimmed and empty entries are dropped.

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
