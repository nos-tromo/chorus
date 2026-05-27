"""Orchestrator end-to-end against a fake adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest
from neo4j import Driver

from tests.ingestion._fakes import FakeAdapter


def test_orchestrator_writes_all_stages(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every artifact + profile stage writes its row to the graph.

    Runs the orchestrator against a fake adapter that yields one row
    per stage and asserts the resulting graph contains the expected
    node counts, the ``[:ON]`` edge from comment to posting, and the
    profile enrichment landed on the author created by the postings
    stage. NER is disabled (no GLiNER service available in the test
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
        "connections": 0,
        "mentions": 0,
    }
    assert set(result["skipped"]) == {"mentions", "connections"}

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
    with migrated_driver.session() as s:
        assert s.run("MATCH (c:Comment) RETURN count(c) AS c").single()["c"] == 0  # type: ignore[index]


def test_connections_stage_skipped(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """Connections stage records itself as skipped without crashing.

    The fake adapter raises ``NotImplementedError`` from the
    connections fetcher; the orchestrator must catch this, log it,
    and add ``"connections"`` to the ``skipped`` list rather than
    propagating the exception. NER is disabled to keep the test off
    the network.

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

    result = run_once(
        FakeAdapter(connections_raises=True),
        migrated_driver,
        raw,
        load_retention_env(),
    )
    assert "connections" in result["skipped"]
    assert result["counts"]["connections"] == 0
