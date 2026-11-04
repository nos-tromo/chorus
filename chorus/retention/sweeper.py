"""The retention sweep: delete expired posts and every copy chorus keeps of them.

A post (posting, comment or chat message) expires when its
``retention_until`` has passed: ``ingested_at`` plus
``RETENTION_DAYS_DEFAULT`` days, stamped once at ingestion (ADR 0011).
Nothing is deleted until an operator switches the sweeper on
(``RETENTION_SWEEP_ENABLED``); :mod:`chorus.retention.report` shows what a
sweep would delete before that.

One sweep deletes, in this order:

1. **Expired posts and what goes with them**, in bounded batches. A post
   takes along the comments on it (when it is a posting) and every reply to
   it or to them, whatever their own deadline: a comment's raw row repeats
   its posting's text, and a reply's its parent's. With them go what only
   those posts referenced — their aliases (surface forms extracted from
   their text), hashtags and attachments — and bare parent references (a
   post node a reply created for a parent whose own row never arrived,
   holding nothing but a uuid). The raw-store rows of every deleted post go
   in the same batch.
2. **Raw-store rows no post holds** (``postings``, ``comments`` and
   ``messages`` rows the ingestion skipped, or left by an interrupted
   batch) once they were received longer ago than the retention period.
3. **Staged uploads left behind** under ``CHORUS_HOME/uploads`` (an upload
   whose job never ran) once they are older than the retention period.

Steps 2 and 3 apply the period in force; they are skipped while retention
is off (``RETENTION_ENABLED=false``). Authors, entities, groups and
platforms are kept, and so are the ``profiles`` and ``connections`` raw
rows: author data is retained indefinitely (ADR 0006). A post without a
``retention_until`` is never swept on its own account.

Every deletion is one ``delete`` event (plus continuation events beyond 200
ids): ``retention.sweep`` per batch, ``retention.raw_store`` per page of raw
rows, ``retention.uploads`` per staged upload. A batch's event is written
inside both transactions, the graph's and the raw store's, before either
commits; when it cannot be written both roll back and the sweep stops with
:class:`~chorus.audit.trail.AuditFailure`. The raw store commits first: if
the graph's commit then fails, the posts stay expired and the next sweep
takes them again. A commit that fails after its event was written is
recorded with ``outcome="error"``. Files have no transactions, so an
upload's event is written before it is deleted, followed by an ``error``
event for whatever could not be deleted.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from loguru import logger
from neo4j import Driver, NotificationDisabledClassification, Session, Transaction

from chorus.audit.trail import AuditContext, AuditFailure, subject_refs
from chorus.ingestion.orchestrator import UUID_SHAPE
from chorus.ingestion.raw_store import RawRowRef, RawStore, zero_counts
from chorus.utils.audit_event import Target
from chorus.utils.env_cfg import RetentionConfig, RetentionSweepConfig

#: The audit operation of one batch of expired posts.
SWEEP_OPERATION: Final = "retention.sweep"
#: The audit operation of one page of raw-store rows no post holds.
RAW_STORE_OPERATION: Final = "retention.raw_store"
#: The audit operation of one stale staged upload.
UPLOADS_OPERATION: Final = "retention.uploads"
#: The audit actions a sweep performs.
SWEEP_ACTIONS: Final = ("delete",)
#: The prefix of the staging directories the ingestion upload creates.
STAGING_PREFIX: Final = "chorus-upload-"
#: The lock file, under ``CHORUS_HOME``, that keeps two sweeps apart.
LOCK_FILE: Final = "retention-sweep.lock"

# Expired posts, oldest deadline first, each with the posts that go with it:
# itself, the comments on it and every reply to any of them.
_BATCH_ROOTS: Final = """
MATCH (root:Post) WHERE root.retention_until <= $cutoff
WITH root ORDER BY root.retention_until LIMIT $limit
CALL (root) {
  MATCH (member:Post)-[:ON|REPLIES_TO*0..]->(root)
  RETURN collect(DISTINCT member.uuid) AS members
}
RETURN members
"""

# Per post of a batch: its table, whether it expired itself, the people it
# names (authors, co-authors, quoted users, resolved entities), what it
# references, and its parents outside the batch.
_BATCH_FACTS: Final = """
UNWIND $uuids AS uuid
MATCH (p:Post {uuid: uuid})
OPTIONAL MATCH (a:Author)-[:AUTHORED|CO_AUTHORED|QUOTED_IN]->(p)
WITH p, collect(DISTINCT a.id) AS authors
OPTIONAL MATCH (p)-[:MENTIONS]->(:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH p, authors, collect(DISTINCT e.id) AS entities
OPTIONAL MATCH (p)-[:MENTIONS|HAS_HASHTAG|HAS_ATTACHMENT]->(x)
WITH p, authors, entities, collect(DISTINCT elementId(x)) AS linked
OPTIONAL MATCH (p)-[:ON|REPLIES_TO]->(parent:Post)
WHERE NOT parent.uuid IN $uuids
RETURN p.uuid AS uuid,
       CASE WHEN p:Posting THEN 'postings' WHEN p:Comment THEN 'comments' ELSE 'messages' END AS kind,
       coalesce(p.retention_until <= $cutoff, false) AS expired,
       authors, entities, linked,
       collect(DISTINCT elementId(parent)) AS parents
"""

_DELETE_POSTS: Final = """
UNWIND $uuids AS uuid
MATCH (p:Post {uuid: uuid})
DETACH DELETE p
"""

# Aliases, hashtags and attachments no remaining post references.
_DELETE_LINKED: Final = """
UNWIND $ids AS id
MATCH (x) WHERE elementId(x) = id
  AND NOT EXISTS { (x)<-[:MENTIONS|HAS_HASHTAG|HAS_ATTACHMENT]-(:Post) }
WITH x, CASE WHEN x:Alias THEN 'aliases' WHEN x:Hashtag THEN 'hashtags' ELSE 'attachments' END AS kind
DETACH DELETE x
RETURN kind, count(*) AS n
"""

# Parents left with nothing: never ingested from a row of their own (no
# ingested_at, no text) and no longer referenced by anything.
_DELETE_PARENT_REFS: Final = """
UNWIND $ids AS id
MATCH (s:Post) WHERE elementId(s) = id
  AND s.ingested_at IS NULL AND s.text IS NULL AND NOT EXISTS { (s)--() }
WITH s, s.uuid AS uuid,
     CASE WHEN s:Posting THEN 'postings' WHEN s:Comment THEN 'comments' ELSE 'messages' END AS kind
DETACH DELETE s
RETURN uuid, kind
"""

_PRESENT_POSTS: Final = """
UNWIND $uuids AS uuid
MATCH (p:Post {uuid: uuid})
RETURN p.uuid AS uuid
"""


def quiet_session(driver: Driver) -> Session:
    """Open a session that does not log notices about absent graph schema.

    The retention queries name every relationship type and property the data
    model has; a deployment without co-authors or hashtags, say, makes the
    server warn about each absent one on every run. Those notices are noise
    in the operational log and in an operator's terminal.

    Args:
        driver: Open Neo4j driver.

    Returns:
        A session; close it (``with``) after use.
    """
    return driver.session(notifications_disabled_classifications=[NotificationDisabledClassification.UNRECOGNIZED])


class SweepRefused(Exception):
    """Sweeping is switched off (``RETENTION_SWEEP_ENABLED``); nothing was done."""


class SweepBusy(Exception):
    """Another sweep is running on the same ``CHORUS_HOME``; nothing was done."""


@dataclass
class SweepTotals:
    """What one sweep deleted, filled as it runs so a failed run still reports it.

    Attributes:
        batches: Batches of expired posts committed.
        posts: Posts deleted: expired, with an expired post, bare parent
            references.
        aliases: Aliases deleted with the last post that mentioned them.
        hashtags: Hashtags deleted with the last post that carried them.
        attachments: Attachments deleted with the last post that had them.
        raw_rows: Raw-store rows deleted, of deleted posts and held by none.
        upload_files: Files of stale staged uploads deleted.
        stopped: Whether the sweep stopped early because it was asked to.
    """

    batches: int = 0
    posts: int = 0
    aliases: int = 0
    hashtags: int = 0
    attachments: int = 0
    raw_rows: int = 0
    upload_files: int = 0
    stopped: bool = False

    def as_dict(self) -> dict[str, int | bool]:
        """Return the totals as a mapping, for printing and logging.

        Returns:
            Each field by name.
        """
        return asdict(self)


@dataclass
class _Batch:
    """One batch of posts, as deleted from the graph.

    Attributes:
        posts: Uuids of the posts deleted, bare parent references included.
        kinds: Posts deleted per table.
        expired: Posts whose own deadline had passed.
        parent_refs: Bare parent references deleted.
        authors: Ids of the authors, co-authors and quoted users of the posts.
        entities: Ids of the entities the posts mentioned.
        linked: Aliases, hashtags and attachments deleted.
        raw_rows: Raw-store rows deleted per post table.
    """

    posts: list[str] = field(default_factory=list)
    kinds: dict[str, int] = field(default_factory=zero_counts)
    expired: int = 0
    parent_refs: int = 0
    authors: set[str] = field(default_factory=set)
    entities: set[str] = field(default_factory=set)
    linked: dict[str, int] = field(default_factory=lambda: {"aliases": 0, "hashtags": 0, "attachments": 0})
    raw_rows: dict[str, int] = field(default_factory=dict)


def audit_time(value: datetime) -> str:
    """Format a time the way audit events carry it.

    Args:
        value: A timezone-aware time.

    Returns:
        ISO 8601 UTC with milliseconds and a ``Z``, e.g.
        ``2026-01-31T12:00:00.000Z``.
    """
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@contextmanager
def sweep_lock(home: Path) -> Iterator[None]:
    """Hold the sweep lock of a ``CHORUS_HOME`` for the duration of the block.

    The backend's scheduled sweep and a one-off sweep run in the same
    container, sharing the volume; the lock keeps them from deleting the
    same posts at once.

    Args:
        home: ``CHORUS_HOME``.

    Yields:
        Nothing; the lock is held until the block exits.

    Raises:
        SweepBusy: If another sweep holds the lock.
    """
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SweepBusy("another retention sweep is running") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def rows_without_post(driver: Driver, rows: Sequence[RawRowRef]) -> list[RawRowRef]:
    """Return the raw rows whose post is not in the graph.

    Args:
        driver: Open Neo4j driver.
        rows: Raw rows of the post tables.

    Returns:
        The rows with no ``UUID`` cell or with one no ``:Post`` carries, in
        input order.
    """
    uuids = sorted({row.uuid for row in rows if row.uuid})
    present: set[str] = set()
    if uuids:
        with quiet_session(driver) as session:
            present = {str(record["uuid"]) for record in session.run(_PRESENT_POSTS, uuids=uuids)}
    return [row for row in rows if not row.uuid or row.uuid not in present]


def listable_uuids(rows: Sequence[RawRowRef]) -> list[str]:
    """Return the uuids of raw rows that an audit event may name.

    A skipped row's ``UUID`` cell can hold a fragment of post text (a
    multi-line post mis-split by the CSV reader), so only UUID-shaped cells
    are named; the rest are counted.

    Args:
        rows: Raw rows.

    Returns:
        The distinct UUID-shaped uuids, sorted.
    """
    return sorted({row.uuid for row in rows if row.uuid and UUID_SHAPE.fullmatch(row.uuid)})


def stale_uploads(uploads: Path, received_before: datetime) -> list[Path]:
    """Return the staged uploads not touched since a time.

    Args:
        uploads: ``CHORUS_HOME/uploads``.
        received_before: Only uploads whose directory and files were all last
            modified earlier than this.

    Returns:
        The staging directories, sorted by name.
    """
    if not uploads.is_dir():
        return []
    limit = received_before.timestamp()
    stale: list[Path] = []
    for staging in sorted(uploads.iterdir()):
        if not staging.is_dir() or staging.is_symlink() or not staging.name.startswith(STAGING_PREFIX):
            continue
        newest = max([staging.lstat().st_mtime, *(entry.lstat().st_mtime for entry in staging.iterdir())])
        if newest < limit:
            stale.append(staging)
    return stale


class _Sweep:
    """One sweep run: its configuration, cutoff, audit context and totals."""

    def __init__(
        self,
        driver: Driver,
        raw: RawStore,
        home: Path,
        *,
        audit: AuditContext,
        retention: RetentionConfig,
        config: RetentionSweepConfig,
        cutoff: datetime,
        should_stop: Callable[[], bool],
        totals: SweepTotals,
    ) -> None:
        """Bind the run's collaborators.

        Args:
            driver: Open Neo4j driver.
            raw: The raw store.
            home: ``CHORUS_HOME``.
            audit: The audit context the deletions are recorded under.
            retention: The retention period in force.
            config: The sweeper configuration.
            cutoff: Posts whose deadline is at or before it expire.
            should_stop: Polled between batches; ``True`` ends the run.
            totals: Accumulates what the run deletes.
        """
        self.driver = driver
        self.raw = raw
        self.home = home
        self.audit = audit
        self.retention = retention
        self.config = config
        self.cutoff = cutoff
        self.should_stop = should_stop
        self.totals = totals
        self.base: dict[str, object] = {"period_days": retention.default_days, "cutoff": audit_time(cutoff)}

    def stop_requested(self) -> bool:
        """Return whether the run was asked to stop, noting it in the totals.

        Returns:
            ``True`` when the run should end now.
        """
        if self.should_stop():
            self.totals.stopped = True
        return self.totals.stopped

    def run(self) -> None:
        """Delete expired posts, then raw rows no post holds, then stale uploads.

        Raises:
            AuditFailure: If a deletion could not be recorded; its batch was
                rolled back.
        """
        number = 0
        while not self.stop_requested() and self.graph_batch(number):
            number += 1
        if self.retention.enabled:
            received_before = self.cutoff - timedelta(days=self.retention.default_days)
            self.raw_store(received_before)
            self.uploads(received_before)

    # --- 1. expired posts --------------------------------------------------

    def plan(self, tx: Transaction) -> list[str]:
        """Choose the posts of the next batch.

        Expired posts are taken in deadline order, each with the posts that
        go with it, until the batch size is reached. A post too big for the
        batch on its own is taken alone, so every batch makes progress.

        Args:
            tx: The batch's graph transaction.

        Returns:
            The uuids of the batch's posts, empty when nothing has expired.
        """
        chosen: list[str] = []
        seen: set[str] = set()
        for record in tx.run(_BATCH_ROOTS, cutoff=self.cutoff, limit=self.config.batch_size):
            members = [str(uuid) for uuid in record["members"] if str(uuid) not in seen]
            if chosen and len(chosen) + len(members) > self.config.batch_size:
                break
            chosen.extend(members)
            seen.update(members)
        return chosen

    def delete_from_graph(self, tx: Transaction, uuids: Sequence[str]) -> _Batch:
        """Delete a batch's posts and what only they referenced.

        Args:
            tx: The batch's graph transaction.
            uuids: The posts of the batch.

        Returns:
            What was deleted.
        """
        batch = _Batch()
        linked: set[str] = set()
        parents: set[str] = set()
        for record in tx.run(_BATCH_FACTS, uuids=list(uuids), cutoff=self.cutoff):
            batch.posts.append(str(record["uuid"]))
            batch.kinds[str(record["kind"])] += 1
            batch.expired += 1 if record["expired"] else 0
            batch.authors.update(str(a) for a in record["authors"])
            batch.entities.update(str(e) for e in record["entities"])
            linked.update(str(x) for x in record["linked"])
            parents.update(str(p) for p in record["parents"])
        tx.run(_DELETE_POSTS, uuids=batch.posts).consume()
        for record in tx.run(_DELETE_LINKED, ids=sorted(linked)):
            batch.linked[str(record["kind"])] += int(record["n"])
        for record in tx.run(_DELETE_PARENT_REFS, ids=sorted(parents)):
            batch.posts.append(str(record["uuid"]))
            batch.kinds[str(record["kind"])] += 1
            batch.parent_refs += 1
        return batch

    def batch_criteria(self, number: int, batch: _Batch) -> dict[str, object]:
        """Return a batch event's criteria: the period, the cutoff and the counts.

        Args:
            number: The batch's number within the run.
            batch: What the batch deleted.

        Returns:
            The criteria.
        """
        return {
            **self.base,
            "batch": number,
            "posts": dict(batch.kinds),
            "expired": batch.expired,
            "cascaded": len(batch.posts) - batch.expired - batch.parent_refs,
            "parent_refs": batch.parent_refs,
            **batch.linked,
            "raw_store": dict(batch.raw_rows),
        }

    def graph_batch(self, number: int) -> bool:
        """Delete one batch of expired posts, recorded before it commits.

        Args:
            number: The batch's number within the run.

        Returns:
            ``False`` when nothing is left to delete.

        Raises:
            AuditFailure: If the batch's event could not be written; both
                stores rolled back.
        """
        # A transaction left by a clean exit would commit: every path that
        # does not commit explicitly, after its event, rolls back.
        with quiet_session(self.driver) as session, session.begin_transaction() as tx:
            uuids = self.plan(tx)
            if not uuids:
                tx.rollback()
                return False
            batch = self.delete_from_graph(tx, uuids)
            if not batch.posts:
                tx.rollback()
                logger.warning("retention sweep: a batch of {} expired posts deleted none; stopping", len(uuids))
                return False
            event_id: str | None = None
            try:
                with self.raw.transaction() as conn:
                    batch.raw_rows = self.raw.delete_post_rows(conn, batch.posts)
                    event_id = self.audit.emit_refs(
                        "delete",
                        SWEEP_OPERATION,
                        kind="mixed",
                        ids=subject_refs(
                            authors=sorted(batch.authors), entities=sorted(batch.entities), posts=sorted(batch.posts)
                        ),
                        count=len(batch.posts),
                        criteria=self.batch_criteria(number, batch),
                    )
            except AuditFailure:
                raise
            except Exception:
                if event_id is not None:
                    self.record_failed_commit(event_id, number, batch, deleted="none")
                raise
            try:
                tx.commit()
            except Exception:
                self.record_failed_commit(event_id, number, batch, deleted="raw_store")
                raise
        self.totals.batches += 1
        self.totals.posts += len(batch.posts)
        self.totals.aliases += batch.linked["aliases"]
        self.totals.hashtags += batch.linked["hashtags"]
        self.totals.attachments += batch.linked["attachments"]
        self.totals.raw_rows += sum(batch.raw_rows.values())
        return True

    def record_failed_commit(self, event_id: str | None, number: int, batch: _Batch, *, deleted: str) -> None:
        """Record that a batch whose deletion was recorded did not commit.

        Args:
            event_id: The batch's ``delete`` event.
            number: The batch's number within the run.
            batch: What the batch was to delete.
            deleted: ``none`` when both stores rolled back, ``raw_store``
                when the raw rows were deleted and the posts were not.

        Raises:
            AuditFailure: If this event could not be written either.
        """
        criteria: dict[str, object] = {**self.base, "batch": number, "deleted": deleted}
        if deleted == "raw_store":
            criteria["raw_store"] = dict(batch.raw_rows)
        self.audit.emit(
            "delete",
            SWEEP_OPERATION,
            outcome="error",
            parent_event_id=event_id,
            target=Target(kind="post", count=len(batch.posts)),
            criteria=criteria,
        )

    # --- 2. raw-store rows no post holds -----------------------------------

    def raw_store(self, received_before: datetime) -> None:
        """Delete the post rows no post holds, received before the period began.

        Args:
            received_before: The cutoff minus the retention period.

        Raises:
            AuditFailure: If a page's event could not be written; that page
                rolled back.
        """
        after_id = 0
        page = 0
        while not self.stop_requested():
            with self.raw.reading() as conn:
                candidates = self.raw.post_rows_received_before(
                    conn, received_before, after_id=after_id, limit=self.config.batch_size
                )
            if not candidates:
                return
            after_id = candidates[-1].id
            rows = rows_without_post(self.driver, candidates)
            if not rows:
                continue
            self.raw_store_page(page, rows, received_before)
            page += 1

    def raw_store_page(self, page: int, rows: Sequence[RawRowRef], received_before: datetime) -> None:
        """Delete one page of raw rows no post holds, recorded before it commits.

        Args:
            page: The page's number within the run.
            rows: The rows to delete.
            received_before: The cutoff minus the retention period.

        Raises:
            AuditFailure: If the page's event could not be written.
        """
        names = listable_uuids(rows)
        criteria: dict[str, object] = {**self.base, "received_before": audit_time(received_before), "page": page}
        event_id: str | None = None
        deleted: dict[str, int] = {}
        try:
            with self.raw.transaction() as conn:
                deleted = self.raw.delete_rows(conn, rows)
                criteria["rows"] = dict(deleted)
                criteria["unlisted"] = sum(1 for row in rows if not (row.uuid and row.uuid in names))
                event_id = self.audit.emit_refs(
                    "delete",
                    RAW_STORE_OPERATION,
                    kind="post",
                    ids=names,
                    count=sum(deleted.values()),
                    container="raw_store",
                    criteria=criteria,
                )
        except AuditFailure:
            raise
        except Exception:
            if event_id is not None:
                self.audit.emit(
                    "delete",
                    RAW_STORE_OPERATION,
                    outcome="error",
                    parent_event_id=event_id,
                    target=Target(kind="post", count=sum(deleted.values()), container="raw_store"),
                    criteria={**criteria, "deleted": "none"},
                )
            raise
        self.totals.raw_rows += sum(deleted.values())

    # --- 3. stale staged uploads -------------------------------------------

    def uploads(self, received_before: datetime) -> None:
        """Delete staged uploads untouched since before the period began.

        Each upload's event is written before its files are deleted; an
        ``error`` event names whatever could not be deleted.

        Args:
            received_before: The cutoff minus the retention period.

        Raises:
            AuditFailure: If an event could not be written; that upload, and
                every one after it, stays.
        """
        criteria: dict[str, object] = {**self.base, "received_before": audit_time(received_before)}
        for staging in stale_uploads(self.home / "uploads", received_before):
            if self.stop_requested():
                return
            names = sorted(entry.name for entry in staging.iterdir())
            if not names:
                with contextlib.suppress(OSError):
                    staging.rmdir()
                continue
            event_id = self.audit.emit_refs(
                "delete",
                UPLOADS_OPERATION,
                kind="file",
                ids=names,
                count=len(names),
                container="uploads",
                criteria=criteria,
            )
            shutil.rmtree(staging, ignore_errors=True)
            left = sorted(entry.name for entry in staging.iterdir()) if staging.is_dir() else []
            if staging.exists():
                self.audit.emit_refs(
                    "delete",
                    UPLOADS_OPERATION,
                    outcome="error",
                    kind="file",
                    ids=left,
                    count=len(left),
                    container="uploads",
                    criteria=criteria,
                    parent_event_id=event_id,
                )
            self.totals.upload_files += len(names) - len(left)


def sweep(
    driver: Driver,
    raw: RawStore,
    home: Path,
    *,
    audit: AuditContext,
    retention: RetentionConfig,
    config: RetentionSweepConfig,
    now: datetime | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    totals: SweepTotals | None = None,
) -> SweepTotals:
    """Run one retention sweep and record every deletion.

    Args:
        driver: Open Neo4j driver.
        raw: The raw store.
        home: ``CHORUS_HOME`` (sweep lock, staged uploads).
        audit: The audit context the deletions are recorded under: the
            system for the backend's scheduled sweep, the operator for a
            one-off command.
        retention: The retention period in force.
        config: The sweeper configuration.
        now: The cutoff; defaults to the current time.
        should_stop: Polled between batches; ``True`` ends the run early.
        totals: Accumulator for what the run deletes, filled as it runs.

    Returns:
        What the run deleted.

    Raises:
        SweepRefused: If sweeping is switched off.
        SweepBusy: If another sweep is running.
        AuditFailure: If a deletion could not be recorded. Its batch rolled
            back and the run stopped; what earlier batches deleted stays
            deleted and recorded.
    """
    if not config.enabled:
        raise SweepRefused("retention sweeping is switched off (RETENTION_SWEEP_ENABLED)")
    totals = totals if totals is not None else SweepTotals()
    cutoff = (now or datetime.now(UTC)).astimezone(UTC)
    with sweep_lock(home):
        try:
            raw.init_schema()
            _Sweep(
                driver,
                raw,
                home,
                audit=audit,
                retention=retention,
                config=config,
                cutoff=cutoff,
                should_stop=should_stop,
                totals=totals,
            ).run()
        except AuditFailure:
            _log_totals(totals, "halted: an audit event could not be written, nothing more is deleted until one can")
            raise
        except Exception as exc:
            # The exception's type only: a driver message can quote a value.
            _log_totals(totals, f"failed ({type(exc).__name__})")
            raise
    _log_totals(totals, "stopped early" if totals.stopped else "done")
    return totals


def _log_totals(totals: SweepTotals, outcome: str) -> None:
    """Write a run's outcome and counts to the operational log, nothing else.

    Args:
        totals: What the run deleted.
        outcome: How it ended.
    """
    log = logger.info if outcome in {"done", "stopped early"} else logger.error
    log(
        "retention sweep {}: {} posts in {} batches, {} aliases, {} hashtags, {} attachments, "
        "{} raw-store rows, {} upload files deleted",
        outcome,
        totals.posts,
        totals.batches,
        totals.aliases,
        totals.hashtags,
        totals.attachments,
        totals.raw_rows,
        totals.upload_files,
    )


def describe_config(retention: RetentionConfig, config: RetentionSweepConfig) -> Mapping[str, Any]:
    """Return the settings a sweep runs under, for reports and logs.

    Args:
        retention: The retention period in force.
        config: The sweeper configuration.

    Returns:
        The switch, interval, batch size and period. No paths.
    """
    return {
        "sweep_enabled": config.enabled,
        "interval_s": config.interval_s,
        "batch_size": config.batch_size,
        "retention_enabled": retention.enabled,
        "period_days": retention.default_days,
    }
