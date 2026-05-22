"""Orchestrator end-to-end against a fake adapter."""

from __future__ import annotations

from typing import Any, Iterable

from neo4j import Driver


class _FakeAdapter:
    def __init__(self, *, connections_raises: bool = True) -> None:
        self._connections_raises = connections_raises

    def fetch_postings(self, since: Any) -> Iterable[dict[str, Any]]:
        yield {
            "UUID": "p-1",
            "Text Content": "hello berlin",
            "Timestamp": "2026-05-01T10:00:00+00:00",
            "Crawled at": "2026-05-02T10:00:00+00:00",
            "Author ID": "a-1",
            "Author": "Alice",
            "Network": "linkedin",
            "Tags": "news, politics",
        }

    def fetch_comments(self, since: Any) -> Iterable[dict[str, Any]]:
        yield {
            "UUID": "c-1",
            "Text Content": "great post",
            "Timestamp": "2026-05-01T11:00:00+00:00",
            "Crawled at": "2026-05-02T11:00:00+00:00",
            "Author ID": "a-2",
            "Network": "linkedin",
            "Parent Posting UUID": "p-1",
        }

    def fetch_messages(self, since: Any) -> Iterable[dict[str, Any]]:
        yield {
            "UUID": "m-1",
            "Chat ID": "chat-1",
            "Sender": "a-1",
            "Text": "sync at 3",
            "Timestamp": "2026-05-01T12:00:00+00:00",
            "Network": "signal",
        }

    def fetch_profiles(self, since: Any) -> Iterable[dict[str, Any]]:
        yield {
            "ID": "a-1",
            "UUID": "prof-a-1",
            "Name": "Alice Anderson",
            "Vanity Name": "alice",
            "Profile Type": "person",
            "Network": "linkedin",
            "Bio": "Berlin-based analyst",
            "Date of Birth": "1990-03-15",
            "Tags": "verified, staff",
        }

    def fetch_connections(self, since: Any) -> Iterable[dict[str, Any]]:
        if self._connections_raises:
            raise NotImplementedError("schema pending")
        return iter(())


def test_orchestrator_writes_all_stages(migrated_driver: Driver) -> None:
    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(_FakeAdapter(), migrated_driver, raw, load_retention_env())

    assert result["counts"] == {
        "postings": 1,
        "comments": 1,
        "messages": 1,
        "profiles": 1,
        "connections": 0,
    }
    assert result["skipped"] == ["connections"]

    with migrated_driver.session() as s:
        assert s.run("MATCH (p:Post) RETURN count(p) AS c").single()["c"] == 3
        assert s.run("MATCH (p:Posting) RETURN count(p) AS c").single()["c"] == 1
        assert s.run("MATCH (c:Comment) RETURN count(c) AS c").single()["c"] == 1
        assert s.run("MATCH (m:Message) RETURN count(m) AS c").single()["c"] == 1
        assert (
            s.run("MATCH (:Comment)-[:ON]->(:Posting) RETURN count(*) AS c").single()[
                "c"
            ]
            == 1
        )
        # the profiles stage enriched the author the posting created
        author = s.run("MATCH (a:Author {id: 'a-1'}) RETURN a").single()["a"]
        assert author["display_name"] == "Alice Anderson"
        assert author["bio"] == "Berlin-based analyst"


def test_connections_stage_skipped(migrated_driver: Driver) -> None:
    from chorus.ingestion.orchestrator import run_once
    from chorus.ingestion.raw_store import RawStore
    from chorus.utils.env_cfg import load_path_env, load_retention_env

    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    result = run_once(
        _FakeAdapter(connections_raises=True),
        migrated_driver,
        raw,
        load_retention_env(),
    )
    assert "connections" in result["skipped"]
    assert result["counts"]["connections"] == 0
