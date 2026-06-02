"""Message (chat) DTO + graph write.

Senders normalize to `:Author` nodes — same label as posting/comment
authors, no separate "sender" type. Chat groups are `:Group` nodes
shared with posting groups; the relationship type
distinguishes (`IN_CHAT` vs `IN_GROUP`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.utils.env_cfg import RetentionConfig


class MessageDTO(BaseModel):
    """Validated DTO for one upstream chat-message row.

    Senders are stored as ``:Author`` nodes (same label as posting
    authors). Chat groups are ``:Group`` nodes shared with posting
    groups; only the relationship type distinguishes them (``IN_CHAT``
    vs ``IN_GROUP``).

    Attributes:
        uuid: Canonical chorus identifier.
        chat_id: Group/chat id this message belongs to.
        chat_group: Human-readable chat group name, if known.
        sender_id: Network sender id, joins to ``:Author.id``.
        sender_display_name: Human-readable sender name.
        handle: Author handle derived from the message URL (X/Twitter),
            or ``None`` when it cannot be derived. See ADR 0008.
        text: Body of the message.
        timestamp: Content creation time; optional. Informational only —
            does not drive retention.
        url: Original message URL.
        answers_count: Reply count reported by the upstream.
        reply_to_uuid: UUID of the parent message when this is a
            threaded reply; ``None`` for top-level messages.
        network: Platform name (resolves to ``:Platform``).
        system_tags: Upstream ``Tags`` field as a string list.
        ingested_at: Time chorus ingested this row (chorus-set, not from the
            upstream). The anchor retention is measured from.
        retention_until: Absolute time the nightly sweeper should
            hard-delete this message (``ingested_at`` + the configured
            window); ``None`` when retention is disabled, leaving it
            non-expiring.
    """

    uuid: str
    chat_id: str
    chat_group: str | None = None
    sender_id: str
    sender_display_name: str | None = None
    handle: str | None = None
    text: str
    timestamp: datetime | None = None
    url: str | None = None
    answers_count: int | None = None
    reply_to_uuid: str | None = None
    network: str
    system_tags: list[str] = Field(default_factory=list)
    ingested_at: datetime
    retention_until: datetime | None = None


def from_row(row: dict[str, Any], retention: RetentionConfig, ingested_at: datetime | None = None) -> MessageDTO:
    """Adapt one upstream message row to a :class:`MessageDTO`.

    ``timestamp`` is optional and informational; ``retention_until`` anchors
    on ``ingested_at`` (the chorus-set ingestion time), uniformly with
    postings and comments.

    Args:
        row: One raw row as returned by the upstream adapter, augmented
            with ``Reply To UUID`` if applicable.
        retention: Retention configuration, used to compute
            ``retention_until``.
        ingested_at: Ingestion time to stamp and anchor retention on;
            defaults to ``datetime.now(UTC)``. The orchestrator passes one
            value per run so a whole run shares a consistent clock.

    Returns:
        A populated, validated :class:`MessageDTO`.

    Raises:
        KeyError: If a required upstream column (``UUID``, ``Chat ID``,
            ``Sender``, ``Network``) is missing.
        ValueError: If a non-empty ``Timestamp`` value is malformed.
        pydantic.ValidationError: If the resulting DTO fails validation.
    """
    ingested_at = ingested_at or datetime.now(UTC)
    ts = _coerce_dt_opt(row.get("Timestamp"))
    return MessageDTO(
        uuid=row["UUID"],
        chat_id=str(row["Chat ID"]),
        chat_group=row.get("Chat Group"),
        sender_id=str(row["Sender"]),
        sender_display_name=row.get("Sender"),
        # X/Twitter status URLs encode the handle; the messages table has
        # no handle column otherwise. See ADR 0008.
        handle=_handle_from_url(row.get("URL")),
        text=row.get("Text") or "",
        timestamp=ts,
        url=row.get("URL"),
        answers_count=_int_or_none(row.get("Answers Count")),
        # The messages table has no separate Message ID column upstream —
        # ``UUID`` is the only identifier. ``Reply To`` therefore IS the
        # parent message's UUID and can be consumed directly. The
        # ``Reply To UUID`` form is retained for callers that pre-resolve.
        reply_to_uuid=row.get("Reply To UUID") or row.get("Reply To"),
        network=row["Network"],
        system_tags=_tags(row.get("Tags")),
        ingested_at=ingested_at,
        retention_until=retention.until(ingested_at),
    )


def write(driver: Driver, dto: MessageDTO) -> None:
    """Write one :class:`MessageDTO` to the graph idempotently.

    MERGEs sender, platform, and chat group, then links the message
    via ``[:AUTHORED]``, ``[:ON_PLATFORM]``, ``[:IN_CHAT]``, and
    optionally ``[:REPLIES_TO]``.

    Args:
        driver: Open Neo4j driver.
        dto: Validated message DTO to write.
    """
    cypher = """
    MERGE (a:Author {id: $sender_id})
      ON CREATE SET a.display_name = $sender_display_name, a.platform = $network,
                    a.handle = $handle
      ON MATCH SET a.display_name = coalesce(a.display_name, $sender_display_name),
                   a.handle = coalesce(a.handle, $handle)
    MERGE (pl:Platform {name: $network})
    MERGE (g:Group {id: $chat_id})
      ON CREATE SET g.name = $chat_group, g.platform = $network
    MERGE (m:Post:Message {uuid: $uuid})
      ON CREATE SET
        m.text            = $text,
        m.timestamp       = datetime($timestamp),
        m.url             = $url,
        m.answers_count   = $answers_count,
        m.system_tags     = $system_tags,
        m.ingested_at     = datetime($ingested_at),
        m.retention_until = datetime($retention_until)
    MERGE (a)-[:AUTHORED]->(m)
    MERGE (m)-[:ON_PLATFORM]->(pl)
    MERGE (m)-[:IN_CHAT]->(g)
    WITH m
    FOREACH (rt IN CASE WHEN $reply_to_uuid IS NULL THEN []
                        ELSE [$reply_to_uuid] END |
      MERGE (parent:Post:Message {uuid: rt})
      MERGE (m)-[:REPLIES_TO]->(parent)
    )
    """
    with driver.session() as s:
        s.run(cypher, **dto.model_dump(mode="json"))


# X/Twitter hosts whose status URLs encode the author handle as the first
# path segment (``/<handle>/status/<id>``). Extend this set as other
# platforms' URL grammars are confirmed against real exports (ADR 0008).
_X_STATUS_HOSTS = frozenset(
    {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }
)
# First path segments on those hosts that are routes, not handles.
_X_RESERVED_SEGMENTS = frozenset({"i", "home", "search", "hashtag", "intent", "messages", "notifications"})


def _handle_from_url(url: str | None) -> str | None:
    """Derive the author handle latent in a chat-message URL.

    The messages table has no handle column, but X/Twitter status URLs
    encode it as the first path segment
    (``https://x.com/<handle>/status/<id>``). Extraction is deliberately
    conservative: it fires only on a recognized host with a
    ``/<handle>/status/`` shape and rejects reserved route segments, so a
    handle is never invented where the URL grammar is unknown. Other
    platforms are supported by extending :data:`_X_STATUS_HOSTS` once
    their URL shapes are confirmed. See ADR 0008.

    Args:
        url: The message URL, or ``None``.

    Returns:
        The handle string (case preserved), or ``None`` when it cannot
        be derived confidently.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in _X_STATUS_HOSTS:
        return None
    segments = [seg for seg in parsed.path.split("/") if seg]
    if len(segments) >= 2 and segments[1].lower() == "status":
        handle = segments[0]
        if handle.lower() in _X_RESERVED_SEGMENTS:
            return None
        return handle
    return None


def _coerce_dt(value: Any) -> datetime:
    """Coerce ``value`` into an aware UTC :class:`datetime`.

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
