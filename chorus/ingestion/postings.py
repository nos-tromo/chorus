"""Posting (top-level post) DTO and graph write.

Maps the upstream `postings` table to `(:Post:Posting)` plus the
surrounding entity/group/platform/attachment nodes. The Cypher MERGEs
authors, platform, and (optional) group before linking the post.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.utils.env_cfg import RetentionConfig


class PostingDTO(BaseModel):
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
    """Adapt one upstream row to a `PostingDTO`. Field-name mapping mirrors
    the upstream table headers documented in CLAUDE.md §Upstream data format.
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
    """Idempotent write: MERGE Author / Platform / (Group) / Posting; link."""
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
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def _coerce_dt_opt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    return _coerce_dt(value)


def _str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


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
