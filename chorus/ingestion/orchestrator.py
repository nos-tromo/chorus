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

Connections projects social-graph edges (`:FOLLOWS`, `:FRIENDS_WITH`)
between `:Author` nodes via `connections.write_batch`, batched with
UNWIND; see ADR 0007.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from loguru import logger
from neo4j import Driver
from pydantic import ValidationError

from chorus.ingestion import comments as comments_stage
from chorus.ingestion import connections as connections_stage
from chorus.ingestion import extraction as extraction_stage
from chorus.ingestion import messages as messages_stage
from chorus.ingestion import postings as postings_stage
from chorus.ingestion import profiles as profiles_stage
from chorus.ingestion.adapter import UpstreamAdapter
from chorus.ingestion.raw_store import RawStore
from chorus.utils.env_cfg import RetentionConfig, load_ner_client_env

_CONNECTIONS_BATCH_SIZE = 500

T = TypeVar("T")


def _parse_or_skip(
    stage: str, row: dict[str, Any], build: Callable[..., T], *args: Any, dropped: dict[str, int]
) -> T | None:
    """Build a DTO for one row, logging and skipping on row-level parse errors.

    ``build`` is invoked as ``build(row, *args)``; every stage's ``from_row``
    takes the raw row as its first positional argument, so the row is supplied
    once and reused for both the call and the skip-log identifier.

    The raw row has already been persisted by the time stage loops call this
    helper, so skipping a malformed row preserves replay/debuggability without
    aborting the rest of the run. Each skip increments ``dropped[stage]`` so the
    run can report how many rows were lost to malformation per stage.

    Args:
        stage: Stage name, used for logging and as the ``dropped`` key.
        row: Raw upstream row; passed as the first argument to ``build``.
        build: Callable that constructs the DTO from ``(row, *args)``.
        *args: Extra positional arguments forwarded after ``row``.
        dropped: Per-stage malformed-row counter; incremented on a skip.

    Returns:
        The constructed DTO, or ``None`` when the row is malformed.
    """
    try:
        return build(row, *args)
    except (KeyError, ValidationError, ValueError) as exc:
        logger.warning(
            "{} row {} skipped: {}",
            stage,
            row.get("UUID") or row.get("ID") or row.get("Network Object ID") or "<unknown>",
            exc,
        )
        dropped[stage] = dropped.get(stage, 0) + 1
        return None


def run_once(
    adapter: UpstreamAdapter,
    driver: Driver,
    raw: RawStore,
    retention: RetentionConfig,
    *,
    since: datetime | None = None,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    """Run every ingestion stage once, in dependency order.

    Stage order is postings → comments → messages → profiles →
    connections. Each stage writes raw rows to the raw store before
    projecting them into the graph, so re-ingestion is possible without
    re-fetching. After each post-like row lands in the graph, the
    extraction stage runs NER over its text and writes ``:MENTIONS``
    edges — unless ``NER_ENABLED=false``, in which case ``"mentions"``
    appears in ``skipped``. The connections stage projects social-graph
    edges in batches; ``counts["connections"]`` reports edges written
    (not rows ingested), since a single row can produce zero, one, two,
    or three edges depending on its Friend/Follower/Following flags.

    Args:
        adapter: Upstream adapter to pull rows from.
        driver: Open Neo4j driver for graph writes.
        raw: Raw-row store for verbatim upstream rows.
        retention: Retention configuration applied to each artifact DTO.
        since: If provided, restrict the pull to rows newer than this
            timestamp; ``None`` means a full pull.
        ingested_at: Chorus-side ingestion time stamped on every artifact and
            used as the retention anchor. Computed once here (defaulting to
            ``datetime.now(UTC)``) so a whole run shares one value; injectable
            for deterministic tests.

    Returns:
        A dict with four keys:

        - ``"counts"``: per-stage counts (rows for postings/comments/
          messages/profiles, ``:MENTIONS`` edges for mentions, social-
          graph edges for connections).
        - ``"skipped"``: list of stage names that were skipped (e.g.
          ``["mentions"]`` when NER is disabled).
        - ``"dropped"``: per-stage count of rows skipped because they were
          malformed (failed DTO parsing/validation), so a partial-but-green
          run surfaces its data loss instead of looking like a clean smaller
          pull.
        - ``"filtered"``: per-stage count of rows intentionally not projected
          for structural reasons — ``comments`` whose parent posting was not
          in the batch, and ``connections`` with no edge signal (self-loop or
          no Friend/Follower/Following flag). Expected, not a data-quality
          problem; surfaced for completeness.
    """
    ingested_at = ingested_at or datetime.now(UTC)
    counts: dict[str, int] = {}
    skipped: list[str] = []
    dropped: dict[str, int] = {"postings": 0, "comments": 0, "messages": 0, "profiles": 0, "connections": 0}
    # Structural filters (not malformation): comments with no in-batch parent,
    # connection rows with no edge signal (self-loop or no Friend/Follower/Following flag).
    filtered: dict[str, int] = {"comments": 0, "connections": 0}
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
        spans = extraction_stage.extract_for_post(text, post_uuid, ner_cfg.model_version)
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
        dto = _parse_or_skip("postings", row, postings_stage.from_row, retention, ingested_at, dropped=dropped)
        if dto is None:
            continue
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
        parent_uuid = posting_id_to_uuid.get(str(parent_pid).strip()) if parent_pid else None
        if not parent_uuid:
            logger.warning(
                "comment {} skipped: parent posting (Posting ID={!r}) not in this batch",
                row.get("UUID"),
                parent_pid,
            )
            filtered["comments"] += 1
            continue
        # Augment a copy so the raw_store payload stays verbatim.
        augmented = dict(row)
        augmented["Parent Posting UUID"] = parent_uuid
        parent_cid = row.get("Parent Comment ID")
        if parent_cid:
            augmented["Parent Comment UUID"] = comment_id_to_uuid.get(str(parent_cid).strip())
        comment_dto = _parse_or_skip(
            "comments", augmented, comments_stage.from_row, retention, ingested_at, dropped=dropped
        )
        if comment_dto is None:
            continue
        comments_stage.write(driver, comment_dto)
        counts["comments"] += 1
        _extract(comment_dto.text, comment_dto.uuid)

    message_rows = list(adapter.fetch_messages(since))
    raw.write_batch("messages", message_rows)
    counts["messages"] = 0
    for row in message_rows:
        message_dto = _parse_or_skip("messages", row, messages_stage.from_row, retention, ingested_at, dropped=dropped)
        if message_dto is None:
            continue
        messages_stage.write(driver, message_dto)
        counts["messages"] += 1
        _extract(message_dto.text, message_dto.uuid)

    profile_rows = list(adapter.fetch_profiles(since))
    raw.write_batch("profiles", profile_rows)
    counts["profiles"] = 0
    for row in profile_rows:
        profile_dto = _parse_or_skip("profiles", row, profiles_stage.from_row, dropped=dropped)
        if profile_dto is None:
            continue
        profiles_stage.write(driver, profile_dto)
        counts["profiles"] += 1

    connection_rows = list(adapter.fetch_connections(since))
    raw.write_batch("connections", connection_rows)
    counts["connections"] = 0
    connection_batch: list[connections_stage.ConnectionDTO] = []
    for row in connection_rows:
        before_dropped = dropped["connections"]
        connection_dto = _parse_or_skip("connections", row, connections_stage.from_row, dropped=dropped)
        if connection_dto is None:
            # A None that did NOT increment dropped came from from_row returning
            # None (no edge signal: self-loop or no flag), not a parse error —
            # count it as a structural filter rather than malformation.
            if dropped["connections"] == before_dropped:
                filtered["connections"] += 1
            continue
        connection_batch.append(connection_dto)
        if len(connection_batch) >= _CONNECTIONS_BATCH_SIZE:
            result = connections_stage.write_batch(driver, connection_batch)
            counts["connections"] += result["follows"] + result["friends_with"]
            connection_batch.clear()
    if connection_batch:
        result = connections_stage.write_batch(driver, connection_batch)
        counts["connections"] += result["follows"] + result["friends_with"]

    total_dropped = sum(dropped.values())
    if total_dropped:
        logger.warning(
            "ingestion dropped {} malformed row(s): {}",
            total_dropped,
            {stage: n for stage, n in dropped.items() if n},
        )
    total_filtered = sum(filtered.values())
    if total_filtered:
        logger.info(
            "ingestion filtered {} row(s) (no in-batch parent / no edge signal): {}",
            total_filtered,
            {stage: n for stage, n in filtered.items() if n},
        )
    return {"counts": counts, "skipped": skipped, "dropped": dropped, "filtered": filtered}
