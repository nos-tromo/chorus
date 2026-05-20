"""Message (chat) DTO + graph write.

Senders normalize to `:Author` nodes — same label as posting/comment
authors, no separate "sender" type. Chat groups are `:Group` nodes
shared with posting groups; the relationship type
distinguishes (`IN_CHAT` vs `IN_GROUP`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.utils.env_cfg import RetentionConfig


class MessageDTO(BaseModel):
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
    ts = _coerce_dt(row["Timestamp"])
    return MessageDTO(
        uuid=row["UUID"],
        chat_id=str(row["Chat ID"]),
        chat_group=row.get("Chat Group"),
        sender_id=str(row["Sender"]),
        sender_display_name=row.get("Sender Display Name"),
        text=row.get("Text") or "",
        timestamp=ts,
        url=row.get("URL"),
        answers_count=_int_or_none(row.get("Answers Count")),
        reply_to_uuid=row.get("Reply To UUID"),
        network=row["Network"],
        system_tags=_tags(row.get("Tags")),
        retention_until=ts + timedelta(days=retention.default_days),
    )


def write(driver: Driver, dto: MessageDTO) -> None:
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
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _tags(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [t.strip() for t in str(value).split(",") if t.strip()]
