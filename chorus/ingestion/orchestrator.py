"""Ingestion stage runner.

Stage order: postings → comments → messages → profiles → connections.
Comments depend on parent postings existing (the comment write MERGEs
the parent posting node, so out-of-order arrival doesn't crash, but
order is still preferred to keep the audit story linear). Profiles runs
after the artifact stages so it enriches `:Author` nodes they have
already created.

Entity extraction (NER) runs inline against each post's text body
during the postings/comments/messages stages: the resulting `:MENTIONS`
edges live next to the post that produced them, and a missing or
disabled GLiNER service skips the step without aborting ingestion. It
is gated by `NER_ENABLED` so dev environments without a reachable
service can opt out cleanly rather than logging per-post warnings.

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
from chorus.ingestion import extraction as extraction_stage
from chorus.ingestion import messages as messages_stage
from chorus.ingestion import postings as postings_stage
from chorus.ingestion import profiles as profiles_stage
from chorus.ingestion.adapter import UpstreamAdapter
from chorus.ingestion.raw_store import RawStore
from chorus.utils.env_cfg import RetentionConfig, load_ner_client_env


def run_once(
    adapter: UpstreamAdapter,
    driver: Driver,
    raw: RawStore,
    retention: RetentionConfig,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Run every ingestion stage once, in dependency order.

    Stage order is postings → comments → messages → profiles →
    connections. Each stage writes raw rows to the raw store before
    projecting them into the graph, so re-ingestion is possible without
    re-fetching. After each post-like row lands in the graph, the
    extraction stage runs NER over its text and writes ``:MENTIONS``
    edges — unless ``NER_ENABLED=false``, in which case ``"mentions"``
    appears in ``skipped``. The connections stage is similarly stubbed:
    it logs and records itself in ``skipped`` rather than raising, so
    the orchestrator can complete end-to-end against any upstream
    snapshot.

    Args:
        adapter: Upstream adapter to pull rows from.
        driver: Open Neo4j driver for graph writes.
        raw: Raw-row store for verbatim upstream rows.
        retention: Retention configuration applied to each artifact DTO.
        since: If provided, restrict the pull to rows newer than this
            timestamp; ``None`` means a full pull.

    Returns:
        A dict with two keys:

        - ``"counts"``: per-stage row counts written to the graph
          (``{"postings": int, "comments": int, ..., "mentions": int}``).
        - ``"skipped"``: list of stage names that were skipped (e.g.
          ``["connections"]`` or ``["mentions", "connections"]``).
    """

    counts: dict[str, int] = {}
    skipped: list[str] = []
    ner_cfg = load_ner_client_env()
    counts["mentions"] = 0
    if not ner_cfg.enabled:
        skipped.append("mentions")

    def _extract(text: str, post_uuid: str) -> None:
        """Run NER on a single post and write the resulting MENTIONS edges.

        No-op when ``NER_ENABLED=false`` or when the text is empty.
        Increments ``counts["mentions"]`` by the number of edges
        actually written.

        Args:
            text: Post body to extract from.
            post_uuid: UUID of the post the spans attach to.
        """
        if not ner_cfg.enabled or not text:
            return
        spans = extraction_stage.extract_for_post(
            text, post_uuid, ner_cfg.model_version
        )
        counts["mentions"] += extraction_stage.write_mentions(driver, post_uuid, spans)

    # The comments stage needs the parent posting's chorus UUID, but the
    # upstream emits the parent reference as ``Posting ID`` (its own
    # primary key for postings, kept on each posting row). Build a
    # lookup as postings are written so the comments loop can augment
    # rows with ``Parent Posting UUID`` before parsing.
    posting_id_to_uuid: dict[str, str] = {}
    posting_rows = list(adapter.fetch_postings(since))
    raw.write_batch("postings", posting_rows)
    counts["postings"] = 0
    for row in posting_rows:
        dto = postings_stage.from_row(row, retention)
        postings_stage.write(driver, dto)
        counts["postings"] += 1
        _extract(dto.text, dto.uuid)
        pid = row.get("Posting ID")
        if pid:
            posting_id_to_uuid[str(pid).strip()] = dto.uuid

    comment_rows = list(adapter.fetch_comments(since))
    raw.write_batch("comments", comment_rows)
    # Pre-pass to build Comment ID → UUID so REPLIES_TO can resolve
    # regardless of arrival order within the batch.
    comment_id_to_uuid: dict[str, str] = {}
    for row in comment_rows:
        cid = row.get("Comment ID")
        cuuid = row.get("UUID")
        if cid and cuuid:
            comment_id_to_uuid[str(cid).strip()] = str(cuuid)

    counts["comments"] = 0
    for row in comment_rows:
        parent_pid = row.get("Posting ID")
        parent_uuid = (
            posting_id_to_uuid.get(str(parent_pid).strip()) if parent_pid else None
        )
        if not parent_uuid:
            logger.warning(
                "comment {} skipped: parent posting (Posting ID={!r}) not in this batch",
                row.get("UUID"),
                parent_pid,
            )
            continue
        # Augment a copy so the raw_store payload stays verbatim.
        augmented = dict(row)
        augmented["Parent Posting UUID"] = parent_uuid
        parent_cid = row.get("Parent Comment ID")
        if parent_cid:
            augmented["Parent Comment UUID"] = comment_id_to_uuid.get(
                str(parent_cid).strip()
            )
        comment_dto = comments_stage.from_row(augmented, retention)
        comments_stage.write(driver, comment_dto)
        counts["comments"] += 1
        _extract(comment_dto.text, comment_dto.uuid)

    message_rows = list(adapter.fetch_messages(since))
    raw.write_batch("messages", message_rows)
    counts["messages"] = 0
    for row in message_rows:
        message_dto = messages_stage.from_row(row, retention)
        messages_stage.write(driver, message_dto)
        counts["messages"] += 1
        _extract(message_dto.text, message_dto.uuid)

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
