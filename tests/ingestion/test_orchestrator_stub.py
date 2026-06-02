"""Orchestrator end-to-end against a fake adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest
from neo4j import Driver

from tests.ingestion._fakes import FakeAdapter


def test_orchestrator_writes_all_stages(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every artifact + profile + connections stage writes its row to the graph.

    Runs the orchestrator against a fake adapter that yields one row
    per stage and asserts the resulting graph contains the expected
    node counts, the ``[:ON]`` edge from comment to posting, the
    profile enrichment landed on the author created by the postings
    stage, and the ``:FOLLOWS`` edge emitted by the connections stage.
    NER is disabled (no GLiNER service available in the test
    environment), so ``"mentions"`` ends up in ``skipped`` with a
    zero count and the assertions stay focused on the structural
    stages.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(FakeAdapter(), migrated_driver, raw, load_retention_env())

    assert result["counts"] == {
        "postings": 1,
        "comments": 1,
        "messages": 1,
        "profiles": 1,
        "connections": 1,
        "mentions": 0,
    }
    assert set(result["skipped"]) == {"mentions"}

    with migrated_driver.session() as s:
        assert s.run("MATCH (p:Post) RETURN count(p) AS c").single()["c"] == 3  # type: ignore[index]
        assert s.run("MATCH (p:Posting) RETURN count(p) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (c:Comment) RETURN count(c) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (m:Message) RETURN count(m) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (:Comment)-[:ON]->(:Posting) RETURN count(*) AS c").single()["c"] == 1  # type: ignore[index]
        # the profiles stage enriched the author the posting created
        author = s.run("MATCH (a:Author {id: 'a-1'}) RETURN a").single()["a"]  # type: ignore[index]
        assert author["display_name"] == "Alice Anderson"
        assert author["bio"] == "Berlin-based analyst"
        # the connections stage projected the row's Follower=Yes flag
        follows = s.run(
            "MATCH (u:Author {id: 'a-1'})-[:FOLLOWS]->(t:Author {id: 'a-2'}) RETURN count(*) AS c"
        ).single()["c"]  # type: ignore[index]
        assert follows == 1


def test_orchestrator_writes_mentions_when_ner_enabled(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With NER enabled and a stubbed extractor, ``:MENTIONS`` edges land.

    Stubs ``ner_client.extract_entities`` to return a single
    ``EntitySpan`` for each call so the test does not depend on a
    running GLiNER service. Verifies that each of the three post-like
    rows (posting, comment, message) produces a ``:MENTIONS`` edge,
    that the ``Alias`` node is MERGEd by surface form, and that the
    edge carries the configured ``model_version`` for provenance.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "true")
    monkeypatch.setenv("NER_MODEL_VERSION", "gliner-test-v9")

    from chorus.inference import ner_client
    from chorus.inference.ner_client import EntitySpan
    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    monkeypatch.setattr(
        ner_client,
        "extract_entities",
        lambda text, **kw: [EntitySpan(text="Berlin", label="loc", start=0, end=6, confidence=0.9)],
    )

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(FakeAdapter(), migrated_driver, raw, load_retention_env())

    # One stubbed span per post-like row (postings + comments + messages).
    assert result["counts"]["mentions"] == 3
    assert "mentions" not in result["skipped"]

    with migrated_driver.session() as s:
        edge_count = s.run("MATCH (:Post)-[m:MENTIONS]->(:Alias) RETURN count(m) AS c")
        assert edge_count.single()["c"] == 3  # type: ignore[index]
        # Alias is MERGEd by surface form, so a single shared node covers all three.
        alias_count = s.run("MATCH (a:Alias) RETURN count(a) AS c")
        assert alias_count.single()["c"] == 1  # type: ignore[index]
        # model_version is recorded on every edge for re-extraction audits.
        provenance = s.run(
            "MATCH (:Post)-[m:MENTIONS]->(:Alias {surface_form: 'Berlin'}) "
            "RETURN collect(DISTINCT m.model_version) AS versions"
        ).single()["versions"]  # type: ignore[index]
        assert provenance == ["gliner-test-v9"]


def test_comment_with_unresolvable_parent_is_skipped(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """A comment whose ``Posting ID`` is not in this batch is logged and skipped.

    The upstream emits the parent posting reference as ``Posting ID``
    (its own primary key), which the orchestrator resolves to chorus's
    UUID via the postings already written in this run. When the
    referenced posting is missing — older than the cutoff, deleted,
    crawl order out of sync — the comment is dropped with a warning
    rather than landing as an orphaned node.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    class _OrphanedCommentAdapter(FakeAdapter):
        """FakeAdapter variant whose comment references an unknown posting."""

        def fetch_comments(self, since: Any) -> Iterable[dict[str, Any]]:
            yield {
                "UUID": "c-orphan",
                "Comment ID": "comment-net-orphan",
                "Text Content": "orphan",
                "Timestamp": "2026-05-01T11:00:00+00:00",
                "Crawled at": "2026-05-02T11:00:00+00:00",
                "Author ID": "a-2",
                "Network": "linkedin",
                "Posting ID": "post-net-does-not-exist",
            }

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(
        _OrphanedCommentAdapter(),
        migrated_driver,
        raw,
        load_retention_env(),
    )

    assert result["counts"]["comments"] == 0
    # the orphan is surfaced as a structural filter, distinct from malformed drops
    assert result["filtered"]["comments"] == 1
    assert result["dropped"]["comments"] == 0
    with migrated_driver.session() as s:
        assert s.run("MATCH (c:Comment) RETURN count(c) AS c").single()["c"] == 0  # type: ignore[index]


def test_malformed_posting_row_is_skipped_and_does_not_abort(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely malformed posting row is logged and skipped without aborting.

    Raw rows are persisted before DTO parsing, so a single bad row should not
    take down the entire run. Here the posting omits the required ``Author
    ID`` (a ``KeyError`` at parse time). The orchestrator drops it, continues
    with the remaining stages, and the dependent comment — whose parent
    ``Posting ID`` is this dropped posting — falls out because its parent was
    never written.

    A missing/blank ``Timestamp`` is deliberately NOT used as the malformed
    case: that is now valid input, kept with a null timestamp (see
    ``test_posting_with_missing_timestamp_is_kept``).

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    class _BadPostingAdapter(FakeAdapter):
        """FakeAdapter variant whose posting omits the required Author ID."""

        def fetch_postings(self, since: Any) -> Iterable[dict[str, Any]]:
            yield {
                "UUID": "p-bad",
                # The default comment points its parent at "post-net-1"; the
                # comment must drop because THIS posting is skipped.
                "Posting ID": "post-net-1",
                "Text Content": "broken row",
                "Timestamp": "2026-05-01T10:00:00+00:00",
                "Crawled at": "2026-05-02T10:00:00+00:00",
                # "Author ID" intentionally omitted -> KeyError in from_row.
                "Network": "linkedin",
            }

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(_BadPostingAdapter(), migrated_driver, raw, load_retention_env())

    assert result["counts"]["postings"] == 0
    assert result["counts"]["comments"] == 0
    assert result["counts"]["messages"] == 1
    assert result["counts"]["profiles"] == 1
    assert result["counts"]["connections"] == 1
    # the malformed posting is surfaced as a per-stage drop, not silently lost
    assert result["dropped"]["postings"] == 1
    assert result["dropped"]["messages"] == 0

    with migrated_driver.session() as s:
        assert s.run("MATCH (p:Posting) RETURN count(p) AS c").single()["c"] == 0  # type: ignore[index]
        assert s.run("MATCH (m:Message) RETURN count(m) AS c").single()["c"] == 1  # type: ignore[index]


def test_posting_with_missing_timestamp_is_kept(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """A posting with no Timestamp is ingested, not dropped; retention uses crawl time.

    Content creation time is optional upstream. The posting lands with a null
    ``timestamp`` and a ``retention_until`` anchored on ``Crawled at`` instead
    of being skipped.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    class _NoTimestampPostingAdapter(FakeAdapter):
        """FakeAdapter variant whose posting carries no creation timestamp."""

        def fetch_postings(self, since: Any) -> Iterable[dict[str, Any]]:
            yield {
                "UUID": "p-1",
                "Posting ID": "post-net-1",
                "Text Content": "no timestamp",
                "Timestamp": None,
                "Crawled at": "2026-05-02T10:00:00+00:00",
                "Author ID": "a-1",
                "Author": "Alice",
                "Network": "linkedin",
            }

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(_NoTimestampPostingAdapter(), migrated_driver, raw, load_retention_env())

    assert result["counts"]["postings"] == 1
    with migrated_driver.session() as s:
        rec = s.run("MATCH (p:Posting {uuid: 'p-1'}) RETURN p.timestamp AS ts, p.retention_until AS ru").single()
        assert rec["ts"] is None  # type: ignore[index]
        assert rec["ru"] is not None  # type: ignore[index]


def test_retention_disabled_writes_no_retention_until(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """With RETENTION_ENABLED=false, a posting lands without a retention deadline.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")
    monkeypatch.setenv("RETENTION_ENABLED", "false")

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(FakeAdapter(), migrated_driver, raw, load_retention_env())

    assert result["counts"]["postings"] == 1
    with migrated_driver.session() as s:
        ru = s.run("MATCH (p:Posting {uuid: 'p-1'}) RETURN p.retention_until AS ru").single()["ru"]  # type: ignore[index]
        assert ru is None


def test_connections_stage_drops_no_signal_rows(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows with no Friend/Follower/Following flag set produce no edges.

    The orchestrator delegates filtering to :func:`connections.from_row`,
    which returns ``None`` for rows that carry no edge signal. The
    stage completes without writing edges and is not added to
    ``skipped``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    class _NoFlagsAdapter(FakeAdapter):
        """FakeAdapter variant whose connection row carries no flag."""

        def fetch_connections(self, since: Any) -> Iterable[dict[str, Any]]:
            yield {
                "Network Object ID": "a-1",
                "Network Object ID selected conn. User": "a-2",
                "Friend": "No",
                "Follower": "No",
                "Following": "No",
                "Crawled at": "2026-05-26T02:31:43+00:00",
            }

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(_NoFlagsAdapter(), migrated_driver, raw, load_retention_env())

    assert result["counts"]["connections"] == 0
    assert "connections" not in result["skipped"]
    # the no-signal row is surfaced as a structural filter, distinct from malformed drops
    assert result["filtered"]["connections"] == 1
    assert result["dropped"]["connections"] == 0
    with migrated_driver.session() as s:
        assert s.run("MATCH ()-[r:FOLLOWS]->() RETURN count(r) AS c").single()["c"] == 0  # type: ignore[index]
        assert s.run("MATCH ()-[r:FRIENDS_WITH]->() RETURN count(r) AS c").single()["c"] == 0  # type: ignore[index]


def test_run_once_stamps_one_ingested_at_across_a_run(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_once stamps a single injected ingested_at on every artifact in the run.

    ingested_at is chorus-set (not read from the upstream row) and computed
    once per run, so all posts/comments/messages from one run share it and
    their retention anchors on it.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("NER_ENABLED", "false")

    from datetime import UTC, datetime

    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    ingested = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    run_once(FakeAdapter(), migrated_driver, raw, load_retention_env(), ingested_at=ingested)

    with migrated_driver.session() as s:
        rec = s.run("MATCH (p:Post) RETURN count(p) AS total, count(DISTINCT p.ingested_at) AS distinct_ia").single()
        assert rec["total"] == 3  # type: ignore[index]
        assert rec["distinct_ia"] == 1  # type: ignore[index]  # one ingested_at shared across the run
        ia = s.run("MATCH (p:Posting {uuid: 'p-1'}) RETURN p.ingested_at AS ia").single()["ia"]  # type: ignore[index]
        assert ia.to_native() == ingested
