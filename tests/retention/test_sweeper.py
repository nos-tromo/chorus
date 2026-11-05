"""The retention sweep against a Neo4j testcontainer and a temporary raw store.

Each test builds a small synthetic graph, sweeps it at ``NOW`` and checks
what was deleted, what was kept, and the ``delete`` events the sweep wrote:
one per batch, inside the batch's transactions, never with content. The
``audit_trail`` fixture verifies the captured chain at teardown; the tests
that break the stream on purpose verify it themselves. All values are
invented.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, override

import neo4j
import neo4j.exceptions
import pytest
from loguru import logger
from neo4j import Driver

from chorus.audit.trail import AuditFailure, AuditTrail
from chorus.ingestion.raw_store import RawStore
from chorus.retention import sweeper
from chorus.retention.sweeper import SweepBusy, SweepRefused, audit_time, sweep, sweep_lock
from tests._audit import CONTENT_KEYS, assert_chain_ok, audit_events, operation_events, started_trail
from tests.retention._graph import (
    NO_DEADLINE,
    NOW,
    OLD,
    PERIOD,
    RECENT,
    RETENTION,
    comment,
    count,
    follows,
    hashtag,
    mention,
    message,
    posting,
    profile,
    raw_store,
    raw_uuids,
    run_sweep,
    sweep_config,
    uuids,
)

_SHAPED = "0f8c4b2e-1d3a-4c5b-9e7f-112233445566"


def _no_content(events: list[dict[str, Any]]) -> None:
    """Assert that events carry no content field and no post text.

    Args:
        events: Captured events.
    """
    for event in events:
        assert not CONTENT_KEYS & set(event), event
    assert "text of" not in json.dumps(events)


def test_nothing_is_deleted_while_switched_off(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """With RETENTION_SWEEP_ENABLED off the sweep refuses before it reads anything."""
    posting(migrated_driver, "p-old", author="author-alice")

    with pytest.raises(SweepRefused):
        sweep(
            migrated_driver,
            raw_store(),
            chorus_env,
            audit=audit_trail.system_context(),
            retention=RETENTION,
            config=sweep_config(enabled=False),
            now=NOW,
        )

    assert uuids(migrated_driver) == {"p-old"}
    assert raw_uuids() == ["p-old"]
    assert operation_events(audit_stream) == []


def test_expired_posting_goes_with_its_comments_replies_and_raw_rows(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """A posting takes its comments and their replies along, whatever their own deadline."""
    d = migrated_driver
    posting(d, "p-old", author="author-alice")
    posting(d, "p-old", author="author-alice", at=RECENT)  # a re-ingest: a second raw row, the same deadline
    comment(d, "c-later", on="p-old", author="author-bob", at=RECENT)
    comment(d, "c-reply", on="p-old", author="author-carol", replies_to="c-later", retention=NO_DEADLINE)
    posting(d, "p-recent", author="author-alice", at=RECENT)
    comment(d, "c-recent", on="p-recent", author="author-bob", at=RECENT)

    totals = run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == {"p-recent", "c-recent"}
    assert raw_uuids() == ["c-recent", "p-recent"]
    assert (totals.batches, totals.posts, totals.raw_rows) == (1, 3, 4)
    events = operation_events(audit_stream)
    assert len(events) == 1
    event = events[0]
    assert (event["action"], event["operation"], event["outcome"]) == ("delete", "retention.sweep", "ok")
    assert event["actor"] == {"user": None, "via": "system"}
    assert "purpose" not in event
    assert event["target"] == {
        "kind": "mixed",
        "ids": [
            "author:author-alice",
            "author:author-bob",
            "author:author-carol",
            "post:c-later",
            "post:c-reply",
            "post:p-old",
        ],
        "count": 3,
    }
    criteria = event["criteria"]
    assert (criteria["period_days"], criteria["cutoff"], criteria["batch"]) == (PERIOD, audit_time(NOW), 0)
    assert criteria["posts"] == {"postings": 1, "comments": 2, "messages": 0}
    assert (criteria["expired"], criteria["cascaded"], criteria["parent_refs"]) == (1, 2, 0)
    assert criteria["raw_store"] == {"postings": 2, "comments": 2, "messages": 0}
    _no_content(events)


def test_message_threads_and_bare_parent_references(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """A message takes its replies along; a parent no row ever filled goes with its last reply."""
    d = migrated_driver
    message(d, "m-old", sender="author-alice")
    message(d, "m-reply", sender="author-bob", replies_to="m-old", at=RECENT)
    message(d, "m-orphan-reply", sender="author-bob", replies_to="m-missing")
    message(d, "m-kept-reply", sender="author-carol", replies_to="m-shared", at=RECENT)
    message(d, "m-gone-reply", sender="author-carol", replies_to="m-shared")
    message(d, "m-recent", sender="author-alice", at=RECENT)

    totals = run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == {"m-kept-reply", "m-shared", "m-recent"}
    assert totals.posts == 5
    events = operation_events(audit_stream)
    assert len(events) == 1
    criteria = events[0]["criteria"]
    assert criteria["posts"] == {"postings": 0, "comments": 0, "messages": 5}
    assert (criteria["expired"], criteria["cascaded"], criteria["parent_refs"]) == (3, 1, 1)
    assert "post:m-missing" in events[0]["target"]["ids"]
    assert criteria["raw_store"] == {"postings": 0, "comments": 0, "messages": 4}


def test_aliases_hashtags_and_attachments_go_with_their_last_post(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """What only deleted posts reference goes; what a kept post references, and entities, stay."""
    d = migrated_driver
    posting(d, "p-old", author="author-alice", Filename="only-old.jpg")
    posting(d, "p-old-2", author="author-alice", Filename="shared.jpg")
    posting(d, "p-recent", author="author-bob", at=RECENT, Filename="shared.jpg")
    mention(d, "p-old", "Only Old Example", entity="entity-1")
    mention(d, "p-old", "Shared Example", entity="entity-2")
    mention(d, "p-recent", "Shared Example")
    hashtag(d, "p-old", "onlyold")
    hashtag(d, "p-old-2", "shared")
    hashtag(d, "p-recent", "shared")

    totals = run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == {"p-recent"}
    with d.session() as s:
        aliases = {r["s"] for r in s.run("MATCH (a:Alias) RETURN a.surface_form AS s")}
        attachments = {r["f"] for r in s.run("MATCH (a:Attachment) RETURN a.filename AS f")}
        hashtags = {r["t"] for r in s.run("MATCH (h:Hashtag) RETURN h.tag AS t")}
    assert (aliases, attachments, hashtags) == ({"Shared Example"}, {"shared.jpg"}, {"shared"})
    assert count(d, "Entity") == 2
    assert (totals.aliases, totals.hashtags, totals.attachments) == (1, 1, 1)
    event = operation_events(audit_stream)[0]
    assert (event["criteria"]["aliases"], event["criteria"]["hashtags"], event["criteria"]["attachments"]) == (1, 1, 1)
    assert event["target"]["ids"][:3] == ["author:author-alice", "entity:entity-1", "entity:entity-2"]
    assert "Only Old Example" not in json.dumps(event)
    assert "only-old.jpg" not in json.dumps(event)


def test_authors_profiles_and_social_edges_are_kept(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """Author data is retained indefinitely (ADR 0006): nodes, profile fields, edges and raw rows."""
    d = migrated_driver
    posting(d, "p-old", author="author-alice", **{"Co-Author": "author-bob", "Quoted User": "author-carol"})
    profile(d, "author-alice")
    follows(d, "author-alice", "author-dave")
    raw_store().write_batch("connections", [{"Network Object ID": "author-alice", "Name": "Alice Example"}])

    run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == set()
    assert count(d, "Author") == 4
    with d.session() as s:
        record = s.run("MATCH (a:Author {id: 'author-alice'})-[:FOLLOWS]->(b) RETURN a.bio AS bio, b.id AS b").single()
    assert record is not None and (record["bio"], record["b"]) == ("an invented biography", "author-dave")
    with raw_store().reading() as conn:
        assert RawStore.count_by_kind(conn) == {"profiles": 1, "connections": 1}
    ids = operation_events(audit_stream)[0]["target"]["ids"]
    assert ids == ["author:author-alice", "author:author-bob", "author:author-carol", "post:p-old"]


def test_posts_without_a_deadline_stay(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """Retention off at ingestion, or a post from before ingestion times: never swept on its own."""
    d = migrated_driver
    posting(d, "p-no-deadline", author="author-alice", retention=NO_DEADLINE)
    with d.session() as s:
        s.run("CREATE (:Post:Posting {uuid: 'p-before-ingested-at', text: 'synthetic'})").consume()

    totals = run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == {"p-no-deadline", "p-before-ingested-at"}
    assert totals.posts == 0
    assert operation_events(audit_stream) == []


def test_batches_are_bounded_and_keep_threads_whole(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """Batches stop at the batch size, but a thread is never split; one too big goes alone."""
    d = migrated_driver
    for i in range(3):
        posting(d, f"p-{i}", author="author-alice", at=OLD + timedelta(minutes=i))
        comment(d, f"c-{i}-a", on=f"p-{i}", author="author-bob", at=RECENT)
        comment(d, f"c-{i}-b", on=f"p-{i}", author="author-bob", at=RECENT)
    posting(d, "p-big", author="author-alice", at=OLD + timedelta(minutes=10))
    for i in range(5):
        comment(d, f"c-big-{i}", on="p-big", author="author-bob", at=RECENT)
    message(d, "m-a", sender="author-carol", at=OLD + timedelta(minutes=20))
    message(d, "m-b", sender="author-carol", at=OLD + timedelta(minutes=21))

    totals = run_sweep(d, chorus_env, audit_trail.system_context(), batch_size=4)

    assert uuids(d) == set()
    events = operation_events(audit_stream)
    assert [e["target"]["count"] for e in events] == [3, 3, 3, 6, 2]
    assert [e["criteria"]["batch"] for e in events] == [0, 1, 2, 3, 4]
    assert totals.batches == 5


def test_more_than_200_ids_continue_on_continuation_events(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """No id is cut: the batch event holds 200, a continuation event the rest."""
    with migrated_driver.session() as s:
        s.run(
            "MERGE (a:Author {id: 'author-alice'}) WITH a UNWIND range(1, 250) AS i "
            "CREATE (m:Post:Message {uuid: 'm-' + toString(i), text: 'synthetic', ingested_at: $at, "
            "retention_until: $deadline}) CREATE (a)-[:AUTHORED]->(m)",
            at=OLD,
            deadline=OLD + timedelta(days=PERIOD),
        ).consume()

    run_sweep(migrated_driver, chorus_env, audit_trail.system_context())

    primary, continuation = operation_events(audit_stream)
    assert (primary["operation"], primary["target"]["count"], len(primary["target"]["ids"])) == (
        "retention.sweep",
        250,
        200,
    )
    assert (continuation["action"], continuation["operation"]) == ("delete", "retention.sweep.ids")
    assert continuation["parent_event_id"] == primary["event_id"]
    assert len(continuation["target"]["ids"]) == 51
    assert "ids_truncated" not in primary["target"]


class _BreakableStream(io.StringIO):
    """A capture buffer whose writes fail while ``broken`` is set."""

    broken = False

    @override
    def write(self, s: str) -> int:
        """Write, or fail like a closed pipe.

        Args:
            s: The text.

        Returns:
            Characters written.

        Raises:
            OSError: While broken.
        """
        if self.broken:
            raise OSError("synthetic broken pipe")
        return super().write(s)


def test_a_failed_audit_write_rolls_back_its_batch_and_stops(migrated_driver: Driver, chorus_env: Path) -> None:
    """No deletion without its record: the batch whose event fails stays, and nothing after it runs.

    The operational log says the sweep halted and what it deleted before, in
    counts only.
    """
    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), level="INFO")
    d = migrated_driver
    posting(d, "p-first", author="author-alice", at=OLD)
    posting(d, "p-second", author="author-alice", at=OLD + timedelta(minutes=1))
    stream = _BreakableStream()
    trail = started_trail(stream)
    original = trail.emitter.emit

    def _emit(*args: Any, **kwargs: Any) -> dict[str, object]:
        if any(e["operation"] == "retention.sweep" for e in audit_events(stream)):
            stream.broken = True
        return original(*args, **kwargs)

    trail.emitter.emit = _emit  # type: ignore[method-assign]
    raw_store().write_batch("comments", [{"UUID": _SHAPED}])

    try:
        with pytest.raises(AuditFailure):
            run_sweep(d, chorus_env, trail.system_context(), batch_size=1)
    finally:
        logger.remove(sink)

    assert uuids(d) == {"p-second"}
    [halted] = [line for line in logs if "retention sweep" in line]
    assert "halted" in halted and "1 posts in 1 batches" in halted
    assert "p-first" not in halted and "author-alice" not in halted
    assert raw_uuids() == [_SHAPED, "p-second"]
    stream.broken = False
    trail.close()
    deletes = operation_events(stream)
    assert [e["operation"] for e in deletes] == ["retention.sweep"]
    assert deletes[0]["target"]["ids"][-1] == "post:p-first"
    assert_chain_ok(stream)


def test_a_failed_graph_commit_is_recorded_and_the_next_sweep_finishes(
    migrated_driver: Driver,
    chorus_env: Path,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw store commits first; when the graph's commit fails, an error event says what went."""
    d = migrated_driver
    posting(d, "p-old", author="author-alice")
    original = neo4j.Transaction.commit
    calls: list[int] = []

    def _commit(self: neo4j.Transaction) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise neo4j.exceptions.ServiceUnavailable("synthetic outage")
        original(self)

    monkeypatch.setattr(neo4j.Transaction, "commit", _commit)

    with pytest.raises(neo4j.exceptions.ServiceUnavailable):
        run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == {"p-old"}
    assert raw_uuids() == []
    recorded, failed = operation_events(audit_stream)
    assert (failed["action"], failed["operation"], failed["outcome"]) == ("delete", "retention.sweep", "error")
    assert failed["parent_event_id"] == recorded["event_id"]
    assert failed["target"] == {"kind": "post", "count": 1}
    assert failed["criteria"]["deleted"] == "raw_store"
    assert failed["criteria"]["raw_store"] == {"postings": 1, "comments": 0, "messages": 0}

    run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == set()
    retried = operation_events(audit_stream)[-1]
    assert (retried["outcome"], retried["criteria"]["raw_store"]) == (
        "ok",
        {"postings": 0, "comments": 0, "messages": 0},
    )


def test_a_failed_raw_store_commit_is_recorded_and_nothing_is_deleted(
    migrated_driver: Driver,
    chorus_env: Path,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the raw store cannot commit, the graph rolls back too, and the event is followed by an error."""
    d = migrated_driver
    posting(d, "p-old", author="author-alice")
    real = RawStore.transaction

    @contextmanager
    def _failing(self: RawStore) -> Iterator[sqlite3.Connection]:
        with real(self) as conn:
            yield conn
            raise sqlite3.OperationalError("synthetic disk I/O error")

    monkeypatch.setattr(RawStore, "transaction", _failing)

    with pytest.raises(sqlite3.OperationalError):
        run_sweep(d, chorus_env, audit_trail.system_context())

    assert uuids(d) == {"p-old"}
    assert raw_uuids() == ["p-old"]
    recorded, failed = operation_events(audit_stream)
    assert failed["outcome"] == "error"
    assert failed["parent_event_id"] == recorded["event_id"]
    assert failed["criteria"]["deleted"] == "none"


def test_raw_rows_no_post_holds_go_once_older_than_the_period(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """Skipped rows go after the period; only UUID-shaped cells are named; author rows stay."""
    raw = raw_store()
    raw.write_batch("comments", [{"UUID": _SHAPED, "Text Content": "text of a skipped comment"}])
    raw.write_batch("postings", [{"UUID": "fragment of a mis-split post", "Text Content": "text of it"}])
    raw.write_batch("messages", [{"Text": "text of a row without a uuid"}])
    raw.write_batch("profiles", [{"ID": "author-alice", "UUID": "profile-author-alice"}])
    posting(migrated_driver, "p-recent", author="author-alice", at=RECENT)

    # Received a moment ago: inside the period, kept.
    run_sweep(migrated_driver, chorus_env, audit_trail.system_context(), now=datetime.now(UTC) + timedelta(days=1))
    assert operation_events(audit_stream) == []

    totals = run_sweep(migrated_driver, chorus_env, audit_trail.system_context())

    assert raw_uuids() == ["p-recent", "profile-author-alice"]
    assert totals.raw_rows == 3
    events = operation_events(audit_stream)
    assert len(events) == 1
    event = events[0]
    assert (event["action"], event["operation"], event["outcome"]) == ("delete", "retention.raw_store", "ok")
    assert event["target"] == {"kind": "post", "ids": [_SHAPED], "count": 3, "container": "raw_store"}
    criteria = event["criteria"]
    assert criteria["rows"] == {"postings": 1, "comments": 1, "messages": 1}
    assert criteria["unlisted"] == 2
    assert criteria["received_before"] == audit_time(NOW - timedelta(days=PERIOD))
    assert "fragment" not in json.dumps(event)
    _no_content(events)


def test_raw_rows_and_uploads_stay_while_retention_is_off(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """With RETENTION_ENABLED off, rows no post holds and staged uploads have no deadline."""
    raw_store().write_batch("comments", [{"UUID": _SHAPED}])
    staging = chorus_env / "uploads" / f"{sweeper.STAGING_PREFIX}zz"
    staging.mkdir(parents=True)
    (staging / "postings.csv").write_text("synthetic", encoding="utf-8")

    run_sweep(migrated_driver, chorus_env, audit_trail.system_context(), retention=NO_DEADLINE)

    assert raw_uuids() == [_SHAPED]
    assert staging.is_dir()
    assert operation_events(audit_stream) == []


def _age(path: Path, when: datetime) -> None:
    """Set a path's modification time.

    Args:
        path: File or directory.
        when: The time to set.
    """
    os.utime(path, (when.timestamp(), when.timestamp()))


def _staging(home: Path, name: str, files: list[str], when: datetime) -> Path:
    """Create a staging directory with files, all last modified at ``when``.

    Args:
        home: ``CHORUS_HOME``.
        name: The directory name.
        files: File names to create.
        when: Their modification time.

    Returns:
        The directory.
    """
    staging = home / "uploads" / name
    staging.mkdir(parents=True)
    for file_name in files:
        (staging / file_name).write_text("synthetic", encoding="utf-8")
        _age(staging / file_name, when)
    _age(staging, when)
    return staging


def test_stale_staged_uploads_are_recorded_then_deleted(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """An upload untouched for longer than the period goes; a newer one and foreign dirs stay."""
    stale = _staging(chorus_env, f"{sweeper.STAGING_PREFIX}old", ["comments.csv", "postings.csv"], OLD)
    fresh = _staging(chorus_env, f"{sweeper.STAGING_PREFIX}new", ["postings.csv"], NOW - timedelta(days=1))
    foreign = _staging(chorus_env, "not-a-staging-dir", ["postings.csv"], OLD)

    totals = run_sweep(migrated_driver, chorus_env, audit_trail.system_context())

    assert not stale.exists()
    assert fresh.is_dir() and foreign.is_dir()
    assert totals.upload_files == 2
    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("delete", "retention.uploads", "ok")
    assert event["target"] == {
        "kind": "file",
        "ids": ["comments.csv", "postings.csv"],
        "count": 2,
        "container": "uploads",
    }
    assert event["criteria"]["received_before"] == audit_time(NOW - timedelta(days=PERIOD))


def test_an_upload_that_survives_deletion_is_recorded_as_an_error(
    migrated_driver: Driver,
    chorus_env: Path,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Files are recorded first, then deleted; whatever is left gets an error event."""
    _staging(chorus_env, f"{sweeper.STAGING_PREFIX}stuck", ["postings.csv"], OLD)
    monkeypatch.setattr(sweeper.shutil, "rmtree", lambda *_a, **_kw: None)

    totals = run_sweep(migrated_driver, chorus_env, audit_trail.system_context())

    recorded, failed = operation_events(audit_stream)
    assert (failed["operation"], failed["outcome"]) == ("retention.uploads", "error")
    assert failed["parent_event_id"] == recorded["event_id"]
    assert failed["target"]["ids"] == ["postings.csv"]
    assert totals.upload_files == 0


def test_two_sweeps_never_run_at_once(migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail) -> None:
    """Two sweeps on one CHORUS_HOME never run at once: the second refuses."""
    posting(migrated_driver, "p-old", author="author-alice")

    with sweep_lock(chorus_env), pytest.raises(SweepBusy):
        run_sweep(migrated_driver, chorus_env, audit_trail.system_context())

    assert uuids(migrated_driver) == {"p-old"}


def test_a_stop_request_ends_the_run_between_batches(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """A shutdown asks the sweep to stop; it does before the next batch."""
    posting(migrated_driver, "p-old", author="author-alice")

    totals = sweep(
        migrated_driver,
        raw_store(),
        chorus_env,
        audit=audit_trail.system_context(),
        retention=RETENTION,
        config=sweep_config(),
        now=NOW,
        should_stop=lambda: True,
    )

    assert totals.stopped and totals.posts == 0
    assert uuids(migrated_driver) == {"p-old"}
    assert operation_events(audit_stream) == []
