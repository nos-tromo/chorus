"""The retention dry run: it counts what a sweep would delete, and nothing more.

The report must agree with what a sweep at the same cutoff then deletes,
must leave the graph, the raw store and the audit trail untouched, and must
carry counts and dates only, never an id, a name or a text. All values are
invented.
"""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from neo4j import Driver

from chorus.audit.trail import AuditTrail
from chorus.retention.report import build_report, render_report
from tests._audit import operation_events
from tests.retention._graph import (
    NO_DEADLINE,
    NOW,
    OLD,
    RECENT,
    RETENTION,
    comment,
    follows,
    mention,
    message,
    posting,
    profile,
    raw_store,
    run_sweep,
    sweep_config,
)
from tests.retention.test_sweeper import _staging

_SHAPED = "0f8c4b2e-1d3a-4c5b-9e7f-112233445566"


def _report(driver: Driver, home: Path, **kwargs: Any) -> dict[str, Any]:
    """Build the report at ``NOW`` against the test's stores.

    Args:
        driver: Driver against the migrated test database.
        home: The test's ``CHORUS_HOME``.
        **kwargs: Overrides for ``retention`` / ``config``.

    Returns:
        The report.
    """
    from chorus.utils.env_cfg import load_path_env

    return build_report(
        driver,
        load_path_env().raw_store,
        home,
        retention=kwargs.get("retention", RETENTION),
        config=kwargs.get("config", sweep_config(enabled=False)),
        now=NOW,
    )


def _raw_rows(path: Path) -> list[tuple[Any, ...]]:
    """Return every raw-store row, read-only.

    Args:
        path: The raw store's file.

    Returns:
        The rows in id order.
    """
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT * FROM raw_rows ORDER BY id").fetchall()
    finally:
        conn.close()


def _mixed_graph(driver: Driver, home: Path) -> None:
    """Write a graph with every case a sweep handles.

    Args:
        driver: Open driver.
        home: ``CHORUS_HOME``.
    """
    posting(driver, "p-old", author="author-alice", Filename="only-old.jpg")
    comment(driver, "c-later", on="p-old", author="author-bob", at=RECENT)
    comment(driver, "c-reply", on="p-old", author="author-carol", replies_to="c-later", retention=NO_DEADLINE)
    posting(driver, "p-recent", author="author-carol", at=RECENT)
    message(driver, "m-orphan-reply", sender="author-bob", replies_to="m-missing")
    message(driver, "m-kept-reply", sender="author-bob", replies_to="m-shared", at=RECENT)
    mention(driver, "p-old", "Only Old Example", entity="entity-1")
    mention(driver, "p-recent", "Shared Example", entity="entity-2")
    mention(driver, "c-later", "Shared Example")
    raw_store().write_batch("comments", [{"UUID": _SHAPED, "Text Content": "text of a skipped comment"}])
    _staging(home, "chorus-upload-old", ["postings.csv"], OLD)


def test_the_report_counts_what_a_sweep_then_deletes(
    migrated_driver: Driver, chorus_env: Path, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """The dry run applies the sweep's rules: its counts are what the sweep deletes."""
    _mixed_graph(migrated_driver, chorus_env)

    report = _report(migrated_driver, chorus_env)
    totals = run_sweep(migrated_driver, chorus_env, audit_trail.system_context())

    delete = report["delete"]
    posts = delete["posts"]
    assert posts["total"] == totals.posts == 5
    assert (posts["postings"], posts["comments"], posts["messages"]) == (1, 2, 2)
    assert (posts["expired"], posts["cascaded"], posts["parent_refs"]) == (2, 2, 1)
    assert posts["deadline_earliest"] == posts["deadline_latest"]
    assert posts["ingested_days_ago_min"] == 25 and posts["ingested_days_ago_max"] == 40
    assert (
        (delete["aliases"], delete["hashtags"], delete["attachments"])
        == (
            totals.aliases,
            totals.hashtags,
            totals.attachments,
        )
        == (1, 0, 1)
    )
    raw = delete["raw_store"]
    assert raw["of_deleted_posts"] == {"postings": 1, "comments": 2, "messages": 1}
    assert {k: raw["without_post"][k] for k in ("postings", "comments", "messages")} == {
        "postings": 0,
        "comments": 1,
        "messages": 0,
    }
    assert sum(raw["of_deleted_posts"].values()) + 1 == totals.raw_rows
    assert delete["uploads"]["directories"] == 1 and delete["uploads"]["files"] == totals.upload_files == 1
    kept = report["keep"]["posts"]
    assert kept["total"] == 3
    assert kept["without_deadline"]["parent_refs"] == 1
    assert kept["next_deadline"] is not None
    assert report["keep"]["entities_left_without_alias"] == 1
    assert report["config"]["sweep_enabled"] is False
    assert [e["operation"] for e in operation_events(audit_stream)] == [
        "retention.sweep",
        "retention.raw_store",
        "retention.uploads",
    ]


def test_the_report_records_and_changes_nothing(migrated_driver: Driver, chorus_env: Path) -> None:
    """No event, no write: the graph and the raw store are as before, the file read-only."""
    from chorus.utils.env_cfg import load_path_env

    _mixed_graph(migrated_driver, chorus_env)
    raw_path = load_path_env().raw_store
    before = _raw_rows(raw_path)
    with migrated_driver.session() as s:
        snapshot = s.run("MATCH (n) RETURN count(n) AS nodes, COUNT { ()-->() } AS edges").single()
    assert snapshot is not None

    first = _report(migrated_driver, chorus_env)
    second = _report(migrated_driver, chorus_env)

    assert first == second
    assert _raw_rows(raw_path) == before
    with migrated_driver.session() as s:
        after = s.run("MATCH (n) RETURN count(n) AS nodes, COUNT { ()-->() } AS edges").single()
    assert after is not None and after.data() == snapshot.data()
    assert (chorus_env / "uploads" / "chorus-upload-old").is_dir()


def test_the_report_never_creates_a_raw_store(migrated_driver: Driver, chorus_env: Path) -> None:
    """Without a raw store on disk the report counts zero rows and leaves none behind."""
    from chorus.utils.env_cfg import load_path_env

    with migrated_driver.session() as s:
        s.run(
            "CREATE (:Post:Posting {uuid: 'p-old', text: 'synthetic', ingested_at: $at, retention_until: $deadline})",
            at=OLD,
            deadline=OLD + timedelta(days=30),
        ).consume()

    report = _report(migrated_driver, chorus_env)

    assert report["delete"]["posts"]["total"] == 1
    assert report["delete"]["raw_store"]["of_deleted_posts"] == {"postings": 0, "comments": 0, "messages": 0}
    assert not load_path_env().raw_store.exists()


def test_the_report_shows_the_authors_a_sweep_leaves_behind(migrated_driver: Driver, chorus_env: Path) -> None:
    """Authors stay (ADR 0006); the report counts who keeps no post, no social edge, profile data."""
    d = migrated_driver
    posting(d, "p-alice", author="author-alice", **{"Quoted User": "author-erin"})
    profile(d, "author-alice")
    posting(d, "p-bob", author="author-bob")
    follows(d, "author-bob", "author-carol")
    posting(d, "p-carol-old", author="author-carol")
    posting(d, "p-carol-new", author="author-carol", at=RECENT)
    profile(d, "author-dave")

    authors = _report(d, chorus_env)["keep"]["authors"]

    assert authors == {
        "of_deleted_posts": 4,
        "left_without_posts": 3,
        "left_with_social_edges_only": 1,
        "left_without_posts_or_social_edges": 2,
        "of_those_with_profile_data": 1,
        "already": {"without_posts_or_social_edges": 1, "of_those_with_profile_data": 1},
    }


def test_the_report_shows_entities_and_groups_left_without_anything(migrated_driver: Driver, chorus_env: Path) -> None:
    """An entity that loses every alias, and a group that loses every post, are counted, not deleted."""
    d = migrated_driver
    posting(d, "p-old", author="author-alice")
    posting(d, "p-new", author="author-alice", at=RECENT)
    mention(d, "p-old", "Gone Example", entity="entity-gone")
    mention(d, "p-old", "Kept Example", entity="entity-kept")
    mention(d, "p-new", "Kept Example")
    mention(d, "p-old", "Untouched Example A", entity="entity-untouched")
    with d.session() as s:
        s.run(
            "MATCH (e:Entity {id: 'entity-untouched'}) CREATE (:Alias {surface_form: 'Untouched Example B'})"
            "-[:RESOLVED_TO]->(e)"
        ).consume()
    message(d, "m-old", sender="author-bob", chat="chat-old")
    message(d, "m-mixed-old", sender="author-bob", chat="chat-mixed")
    message(d, "m-mixed-new", sender="author-bob", chat="chat-mixed", at=RECENT)

    keep = _report(d, chorus_env)["keep"]

    assert keep["entities_left_without_alias"] == 1
    assert keep["groups_left_without_post"] == 1


def test_the_report_shows_posts_kept_without_a_deadline(migrated_driver: Driver, chorus_env: Path) -> None:
    """Posts with no deadline are counted by why they have none."""
    d = migrated_driver
    posting(d, "p-off", author="author-alice", retention=NO_DEADLINE)
    with d.session() as s:
        s.run("CREATE (:Post:Posting {uuid: 'p-before', text: 'synthetic'})").consume()
    message(d, "m-reply", sender="author-bob", replies_to="m-missing", at=RECENT)
    with d.session() as s:
        # A parent whose own row an earlier release never applied: an author, no text, no deadline.
        s.run(
            "MERGE (a:Author {id: 'author-carol'}) CREATE (a)-[:AUTHORED]->(:Post:Message {uuid: 'm-unfilled'})"
        ).consume()

    report = _report(d, chorus_env)

    kept = report["keep"]["posts"]
    assert kept["without_deadline"] == {
        "retention_off": 1,
        "before_ingestion_times": 1,
        "parent_refs": 2,
        "unfilled": 1,
    }
    assert kept["not_due"] == 1
    assert report["delete"]["posts"]["total"] == 0
    assert "1 of them with an author" in render_report(report)


def test_the_report_carries_no_ids_names_or_texts(migrated_driver: Driver, chorus_env: Path) -> None:
    """Counts and dates only, in the JSON numbers and in the rendered text."""
    _mixed_graph(migrated_driver, chorus_env)

    report = _report(migrated_driver, chorus_env)
    text = render_report(report)
    numbers = json.dumps(report)

    for secret in ("p-old", "c-later", "author-alice", "entity-1", "Only Old Example", "only-old.jpg", "text of"):
        assert secret not in text
        assert secret not in numbers
    assert _SHAPED not in numbers
    assert "nothing is recorded or deleted" in text
    assert "Sweeper: off (RETENTION_SWEEP_ENABLED)" in text
    assert "posts: 5 (postings 1, comments 2, messages 2)" in text
    assert "left without any post" in text


def test_the_report_says_what_retention_off_leaves_alone(migrated_driver: Driver, chorus_env: Path) -> None:
    """While retention is off the sweep keeps raw rows and uploads, and the report says so."""
    raw_store().write_batch("comments", [{"UUID": _SHAPED}])

    report = _report(migrated_driver, chorus_env, retention=NO_DEADLINE, config=sweep_config())
    text = render_report(report)

    assert report["delete"]["raw_store"]["without_post"] is None
    assert report["delete"]["uploads"] is None
    assert "raw-store rows no post holds: none (retention is off)" in text
    assert "Sweeper: on, one-off runs only" in text
