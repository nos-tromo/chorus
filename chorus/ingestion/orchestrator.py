"""Ingestion stage runner.

Stage order: postings → comments → messages → profiles → connections.
Comments depend on parent postings existing (the comment write MERGEs
the parent posting node, so out-of-order arrival doesn't crash, but
order is still preferred to keep the audit story linear). Profiles runs
after the artifact stages so it enriches `:Author` nodes they have
already created.

Connections currently logs `skipped: schema pending` instead of raising,
so the orchestrator can run end-to-end against any upstream snapshot
even though that stage is stubbed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from neo4j import Driver

from chorus.ingestion import comments as comments_stage
from chorus.ingestion import connections as connections_stage  # noqa: F401
from chorus.ingestion import messages as messages_stage
from chorus.ingestion import postings as postings_stage
from chorus.ingestion import profiles as profiles_stage
from chorus.ingestion.adapter import UpstreamAdapter
from chorus.ingestion.raw_store import RawStore
from chorus.utils.env_cfg import RetentionConfig


def run_once(
    adapter: UpstreamAdapter,
    driver: Driver,
    raw: RawStore,
    retention: RetentionConfig,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Run all stages once. Returns per-stage counts and a skipped list."""

    counts: dict[str, int] = {}
    skipped: list[str] = []

    posting_rows = list(adapter.fetch_postings(since))
    raw.write_batch("postings", posting_rows)
    counts["postings"] = 0
    for row in posting_rows:
        dto = postings_stage.from_row(row, retention)
        postings_stage.write(driver, dto)
        counts["postings"] += 1

    comment_rows = list(adapter.fetch_comments(since))
    raw.write_batch("comments", comment_rows)
    counts["comments"] = 0
    for row in comment_rows:
        comment_dto = comments_stage.from_row(row, retention)
        comments_stage.write(driver, comment_dto)
        counts["comments"] += 1

    message_rows = list(adapter.fetch_messages(since))
    raw.write_batch("messages", message_rows)
    counts["messages"] = 0
    for row in message_rows:
        message_dto = messages_stage.from_row(row, retention)
        messages_stage.write(driver, message_dto)
        counts["messages"] += 1

    profile_rows = list(adapter.fetch_profiles(since))
    raw.write_batch("profiles", profile_rows)
    counts["profiles"] = 0
    for row in profile_rows:
        profile_dto = profiles_stage.from_row(row)
        profiles_stage.write(driver, profile_dto)
        counts["profiles"] += 1

    try:
        connection_rows = list(adapter.fetch_connections(since))
        raw.write_batch("connections", connection_rows)
    except NotImplementedError as exc:
        logger.info("connections stage skipped: {}", exc)
        skipped.append("connections")
        counts["connections"] = 0
    else:
        logger.warning(
            "connections rows present but ingest writer is stubbed — "
            "{} rows stored in raw_store, not in graph",
            len(connection_rows),
        )
        skipped.append("connections")
        counts["connections"] = 0

    return {"counts": counts, "skipped": skipped}
