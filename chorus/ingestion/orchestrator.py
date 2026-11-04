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

Audit trail: :func:`run_audited` records one pass as one ``collect``
event (whose target lists the authors whose data entered the store, with
per-stage graph counts and per-table raw-store counts as criteria) and,
when NER ran, one ``ai_call`` / ``ingest.ner`` event for the post texts
sent to the NER service.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from loguru import logger
from neo4j import Driver
from pydantic import ValidationError

from chorus.audit.trail import AuditContext
from chorus.ingestion import comments as comments_stage
from chorus.ingestion import connections as connections_stage
from chorus.ingestion import extraction as extraction_stage
from chorus.ingestion import messages as messages_stage
from chorus.ingestion import postings as postings_stage
from chorus.ingestion import profiles as profiles_stage
from chorus.ingestion.adapter import UpstreamAdapter
from chorus.ingestion.raw_store import RawStore
from chorus.utils.audit_event import AiCall, Target
from chorus.utils.env_cfg import RetentionConfig, load_ner_client_env
from chorus.utils.log_redaction import redact_validation_errors

_CONNECTIONS_BATCH_SIZE = 500


@dataclass
class IngestStats:
    """What one ingestion pass has written so far, for its audit events.

    Filled while the pass runs, so a pass that fails still reports what it
    wrote before the failure.

    Attributes:
        graph: Rows (or edges) projected into the graph, per stage.
        raw_rows: Rows persisted to the raw store, per table.
        authors: ``:Author.id`` values whose data the pass wrote: authors,
            co-authors and quoted users of postings, comment authors,
            message senders, enriched profiles, both ends of connections.
        ner_posts: Posts whose text was sent to the NER service.
        ner_model: The NER model recorded as provenance, when NER ran.
    """

    graph: dict[str, int] = field(default_factory=dict)
    raw_rows: dict[str, int] = field(default_factory=dict)
    authors: set[str] = field(default_factory=set)
    ner_posts: int = 0
    ner_model: str | None = None


def ingest_actions() -> tuple[str, ...]:
    """Return the audit actions one ingestion pass performs.

    Returns:
        ``("collect",)``, plus ``"ai_call"`` while NER is enabled (post
        texts go to the NER service).
    """
    return ("collect", "ai_call") if load_ner_client_env().enabled else ("collect",)


_UUID_SHAPE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")

T = TypeVar("T")


def _row_ref(row: dict[str, Any]) -> str:
    """Return a log-safe reference to an upstream row.

    The row's upstream UUID is an internal key and may be logged. Any other
    cell may not, and neither may a ``UUID`` cell that is not UUID-shaped:
    a multi-line post mis-split by the CSV reader leaves a phantom row whose
    first cell is a fragment of the post's text.

    Args:
        row: Raw upstream row.

    Returns:
        The row's ``UUID`` when it is UUID-shaped, ``"<no uuid>"`` when the
        cell is missing or blank, and ``"<malformed uuid>"`` otherwise.
    """
    value = row.get("UUID")
    text = "" if value is None else str(value).strip()
    if not text:
        return "<no uuid>"
    return text if _UUID_SHAPE.fullmatch(text) else "<malformed uuid>"


def _error_shape(exc: Exception) -> list[dict[str, Any]] | str:
    """Describe a row parse error without quoting the row's values.

    Args:
        exc: The ``KeyError``, ``ValidationError`` or ``ValueError`` raised
            while building a row's DTO.

    Returns:
        For a ``ValidationError``, its errors with the submitted values
        redacted (location, type and message remain). For a ``KeyError``,
        the missing column's name: ``from_row`` raises it only for a missing
        required column. For any other ``ValueError``, the exception type's
        name alone, since its message usually quotes the unparseable value.
    """
    # ValidationError subclasses ValueError, so it is checked first.
    if isinstance(exc, ValidationError):
        return redact_validation_errors(exc.errors())
    if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str):
        return f"missing column {exc.args[0]!r}"
    return type(exc).__name__


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
    run can report how many rows were lost to malformation per stage. The skip
    is logged with the row's UUID and the error's shape, never a cell value.

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
        logger.warning("{} row {} skipped: {}", stage, _row_ref(row), _error_shape(exc))
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
    stats: IngestStats | None = None,
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
        stats: Accumulator for what the pass writes, filled as it runs (see
            :class:`IngestStats`); ``counts`` in the result is its ``graph``.

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
    stats = stats if stats is not None else IngestStats()
    counts = stats.graph
    skipped: list[str] = []
    dropped: dict[str, int] = {"postings": 0, "comments": 0, "messages": 0, "profiles": 0, "connections": 0}
    # Structural filters (not malformation): comments with no in-batch parent,
    # connection rows with no edge signal (self-loop or no Friend/Follower/Following flag).
    filtered: dict[str, int] = {"comments": 0, "connections": 0}
    ner_cfg = load_ner_client_env()
    counts["mentions"] = 0
    if ner_cfg.enabled:
        stats.ner_model = ner_cfg.model_version
    else:
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
        stats.ner_posts += 1  # counted before the call: the text leaves with the request
        spans = extraction_stage.extract_for_post(text, post_uuid, ner_cfg.model_version)
        counts["mentions"] += extraction_stage.write_mentions(driver, post_uuid, spans)

    # The comments stage needs the parent posting's chorus UUID, but the
    # upstream emits the parent reference as ``Posting ID`` (its own
    # primary key for postings, kept on each posting row). Build a
    # lookup as postings are written so the comments loop can augment
    # rows with ``Parent Posting UUID`` before parsing.
    posting_id_to_uuid: dict[str, str] = {}
    posting_rows = list(adapter.fetch_postings(since))
    stats.raw_rows["postings"] = raw.write_batch("postings", posting_rows)
    counts["postings"] = 0
    for row in posting_rows:
        dto = _parse_or_skip("postings", row, postings_stage.from_row, retention, ingested_at, dropped=dropped)
        if dto is None:
            continue
        postings_stage.write(driver, dto)
        counts["postings"] += 1
        stats.authors.update(a for a in (dto.author_id, dto.co_author_id, dto.quoted_user_id) if a)
        _extract(dto.text, dto.uuid)
        pid = row.get("Posting ID")
        if pid:
            posting_id_to_uuid[str(pid).strip()] = dto.uuid

    comment_rows = list(adapter.fetch_comments(since))
    stats.raw_rows["comments"] = raw.write_batch("comments", comment_rows)
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
            logger.warning("comment {} skipped: parent posting not in this batch", _row_ref(row))
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
        stats.authors.add(comment_dto.author_id)
        _extract(comment_dto.text, comment_dto.uuid)

    message_rows = list(adapter.fetch_messages(since))
    stats.raw_rows["messages"] = raw.write_batch("messages", message_rows)
    counts["messages"] = 0
    for row in message_rows:
        message_dto = _parse_or_skip("messages", row, messages_stage.from_row, retention, ingested_at, dropped=dropped)
        if message_dto is None:
            continue
        messages_stage.write(driver, message_dto)
        counts["messages"] += 1
        stats.authors.add(message_dto.sender_id)
        _extract(message_dto.text, message_dto.uuid)

    profile_rows = list(adapter.fetch_profiles(since))
    stats.raw_rows["profiles"] = raw.write_batch("profiles", profile_rows)
    counts["profiles"] = 0
    for row in profile_rows:
        profile_dto = _parse_or_skip("profiles", row, profiles_stage.from_row, dropped=dropped)
        if profile_dto is None:
            continue
        profiles_stage.write(driver, profile_dto)
        counts["profiles"] += 1
        stats.authors.add(profile_dto.id)

    connection_rows = list(adapter.fetch_connections(since))
    stats.raw_rows["connections"] = raw.write_batch("connections", connection_rows)
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
            stats.authors.update(a for c in connection_batch for a in (c.row_user_id, c.target_id))
            connection_batch.clear()
    if connection_batch:
        result = connections_stage.write_batch(driver, connection_batch)
        counts["connections"] += result["follows"] + result["friends_with"]
        stats.authors.update(a for c in connection_batch for a in (c.row_user_id, c.target_id))

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


def run_audited(
    adapter: UpstreamAdapter,
    driver: Driver,
    raw: RawStore,
    retention: RetentionConfig,
    *,
    audit: AuditContext,
    operation: str,
    criteria: Mapping[str, object],
    since: datetime | None = None,
) -> dict[str, Any]:
    """Run one ingestion pass and record it on the audit trail.

    Writes one ``collect`` event for the pass (``operation`` names the entry
    point, e.g. ``ingest.upload``): its target lists the authors whose data
    entered the store; its criteria are ``criteria`` plus ``graph`` (rows or
    edges projected per stage) and ``raw_store`` (rows persisted per table).
    When NER ran, one ``ai_call`` / ``ingest.ner`` event follows, parented
    to the collection: how many post texts went to the NER service
    (``recipient="inference:ner"``) and the model recorded as provenance.
    A failed pass writes both with ``outcome="error"`` and what it wrote
    before failing, then re-raises.

    Args:
        adapter: Upstream adapter to pull rows from.
        driver: Open Neo4j driver for graph writes.
        raw: Raw-row store for verbatim upstream rows.
        retention: Retention configuration applied to each artifact DTO.
        audit: The caller's audit context.
        operation: The audit operation, e.g. ``ingest.run``.
        criteria: The caller's parameters (cutoff, source files).
        since: If provided, restrict the pull to rows newer than this.

    Returns:
        :func:`run_once`'s result.

    Raises:
        AuditFailure: If an audit event could not be written.
    """
    stats = IngestStats()
    try:
        out = run_once(adapter, driver, raw, retention, since=since, stats=stats)
    except Exception:
        _record_collection(audit, operation, criteria, stats, outcome="error")
        raise
    _record_collection(audit, operation, criteria, stats, outcome="ok")
    return out


def _record_collection(
    audit: AuditContext,
    operation: str,
    criteria: Mapping[str, object],
    stats: IngestStats,
    *,
    outcome: str,
) -> str:
    """Write a pass's ``collect`` event and, when NER ran, its ``ai_call`` event.

    Args:
        audit: The caller's audit context.
        operation: The audit operation of the pass.
        criteria: The caller's parameters.
        stats: What the pass wrote.
        outcome: ``ok`` or ``error``.

    Returns:
        The ``collect`` event's id.
    """
    collect_id = audit.emit_refs(
        "collect",
        operation,
        outcome=outcome,
        kind="author",
        ids=sorted(stats.authors),
        count=len(stats.authors),
        criteria={**criteria, "graph": dict(stats.graph), "raw_store": dict(stats.raw_rows)},
    )
    if stats.ner_posts:
        audit.emit(
            "ai_call",
            "ingest.ner",
            outcome=outcome,
            parent_event_id=collect_id,
            recipient="inference:ner",
            target=Target(kind="post", count=stats.ner_posts),
            criteria={"mentions": stats.graph.get("mentions", 0)},
            ai=AiCall(model_alias=stats.ner_model or "", sources=[collect_id]),
        )
    return collect_id
