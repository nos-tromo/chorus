"""Message (chat) DTO + graph write.

Senders normalize to `:Author` nodes — same label as posting/comment
authors, no separate "sender" type. Chat groups are `:Group` nodes
shared with posting groups; the relationship type
distinguishes (`IN_CHAT` vs `IN_GROUP`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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
        text: Body of the message.
        timestamp: Content creation time (drives retention).
        url: Original message URL.
        answers_count: Reply count reported by the upstream.
        reply_to_uuid: UUID of the parent message when this is a
            threaded reply; ``None`` for top-level messages.
        network: Platform name (resolves to ``:Platform``).
        system_tags: Upstream ``Tags`` field as a string list.
        retention_until: Absolute time the nightly sweeper should
            hard-delete this message.
    """

    uuid: str
    chat_id: str
    chat_group: str | None = None
    sender_id: str
    sender_display_name: str | None = None
    text: str
    timestamp: datetime
    url: str | None = None
    answers_count: int | None = None
    reply_to_uuid: str | None = None
    network: str
    system_tags: list[str] = Field(default_factory=list)
    retention_until: datetime


def from_row(row: dict[str, Any], retention: RetentionConfig) -> MessageDTO:
    """Adapt one upstream message row to a :class:`MessageDTO`.

    Args:
        row: One raw row as returned by the upstream adapter, augmented
            with ``Reply To UUID`` if applicable.
        retention: Retention configuration, used to compute
            ``retention_until``.

    Returns:
        A populated, validated :class:`MessageDTO`.

    Raises:
        KeyError: If a required upstream column is missing.
        ValueError: If a timestamp column is malformed.
        pydantic.ValidationError: If the resulting DTO fails validation.
    """
    ts = _coerce_dt(row["Timestamp"])
    return MessageDTO(
        uuid=row["UUID"],
        chat_id=str(row["Chat ID"]),
        chat_group=row.get("Chat Group"),
        sender_id=str(row["Sender"]),
        # The messages table carries only ``Sender`` (no separate numeric
        # id or display-name column); it is the sole human-readable
        # identity the table provides, so it populates display_name as
        # well as the (string) id. See ADR 0008 for the identity gap this
        # leaves — message senders cannot be keyed on a network id.
        sender_display_name=row.get("Sender"),
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
        retention_until=ts + timedelta(days=retention.default_days),
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
      ON CREATE SET a.display_name = $sender_display_name, a.platform = $network
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
