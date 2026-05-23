"""Orchestrator end-to-end against a fake adapter."""

from __future__ import annotations

from neo4j import Driver

from tests.ingestion._fakes import FakeAdapter


def test_orchestrator_writes_all_stages(migrated_driver: Driver) -> None:
    """Every artifact + profile stage writes its row to the graph.

    Runs the orchestrator against a fake adapter that yields one row
    per stage and asserts the resulting graph contains the expected
    node counts, the ``[:ON]`` edge from comment to posting, and the
    profile enrichment landed on the author created by the postings
    stage.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
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
    }
    assert result["skipped"] == ["connections"]

    with migrated_driver.session() as s:
        assert s.run("MATCH (p:Post) RETURN count(p) AS c").single()["c"] == 3  # type: ignore[index]
        assert s.run("MATCH (p:Posting) RETURN count(p) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (c:Comment) RETURN count(c) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (m:Message) RETURN count(m) AS c").single()["c"] == 1  # type: ignore[index]
        assert (
            s.run("MATCH (:Comment)-[:ON]->(:Posting) RETURN count(*) AS c").single()[
                "c"
            ]
            == 1
        )  # type: ignore[index]
        # the profiles stage enriched the author the posting created
        author = s.run("MATCH (a:Author {id: 'a-1'}) RETURN a").single()["a"]  # type: ignore[index]
        assert author["display_name"] == "Alice Anderson"
        assert author["bio"] == "Berlin-based analyst"


def test_connections_stage_skipped(migrated_driver: Driver) -> None:
    """Connections stage records itself as skipped without crashing.

    The fake adapter raises ``NotImplementedError`` from the
    connections fetcher; the orchestrator must catch this, log it,
    and add ``"connections"`` to the ``skipped`` list rather than
    propagating the exception.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
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
