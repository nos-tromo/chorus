"""Comment (reply to a posting or another comment) DTO + graph write.

`Posting Text` and `Parent Comment Text` are useful for extraction context
during ingestion but are *not* stored on the comment node — the parent's
text is already on the parent node, and duplicating it would inflate
storage and risk drift.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.utils.env_cfg import RetentionConfig


class CommentDTO(BaseModel):
    uuid: str
    network_object_id: str | None = None
    network_comment_id: str | None = None
    url: str | None = None
    text: str
    timestamp: datetime
    crawled_at: datetime
    author_id: str
    author_display_name: str | None = None
    vanity_name: str | None = None
    replies_count: int | None = None
    reactions_count: int | None = None
    parent_comment_uuid: str | None = None
    parent_posting_uuid: str
    network: str
    system_tags: list[str] = Field(default_factory=list)
    retention_until: datetime


def from_row(row: dict[str, Any], retention: RetentionConfig) -> CommentDTO:
    ts = _coerce_dt(row["Timestamp"])
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
        timestamp=ts,
        crawled_at=_coerce_dt(row["Crawled at"]),
        author_id=str(row["Author ID"]),
        author_display_name=row.get("Author"),
        vanity_name=row.get("Vanity Name"),
        replies_count=_int_or_none(row.get("Replies Count")),
        reactions_count=_int_or_none(row.get("Reactions Count")),
        parent_comment_uuid=row.get("Parent Comment UUID"),
        parent_posting_uuid=row["Parent Posting UUID"],
        network=row["Network"],
        system_tags=_tags(row.get("Tags")),
        retention_until=ts + timedelta(days=retention.default_days),
    )


def write(driver: Driver, dto: CommentDTO) -> None:
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
