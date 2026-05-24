"""Orchestrator end-to-end against a fake adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import Driver


class _FakeAdapter:
    """In-memory :class:`UpstreamAdapter` used by orchestrator tests.

    Yields one canned row per artifact stage so the orchestrator can be
    exercised end-to-end without a real upstream.
    """

    def __init__(self, *, connections_raises: bool = True) -> None:
        """Configure the fake.

        Args:
            connections_raises: When ``True`` (the default), the
                connections fetcher raises ``NotImplementedError`` to
                exercise the orchestrator's skip path. ``False`` yields
                an empty iterator so the "rows present but stubbed"
                branch is covered instead.
        """
        self._connections_raises = connections_raises

    def fetch_postings(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned posting row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row populated with the upstream column names the
            postings DTO maps from.
        """
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
        """Yield one canned comment row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row referencing the canned posting via
            ``Parent Posting UUID``.
        """
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
        """Yield one canned chat-message row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row populated with the upstream column names the
            messages DTO maps from.
        """
        yield {
            "UUID": "m-1",
            "Chat ID": "chat-1",
            "Sender": "a-1",
            "Text": "sync at 3",
            "Timestamp": "2026-05-01T12:00:00+00:00",
            "Network": "signal",
        }

    def fetch_profiles(self, since: Any) -> Iterable[dict[str, Any]]:
        """Yield one canned author-profile row.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Yields:
            A single row referencing the canned posting author by ``ID``.
        """
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
        """Return an empty iterator or raise, per the constructor toggle.

        Args:
            since: Ignored; documented for parity with the real adapter.

        Returns:
            An empty iterator when ``connections_raises`` is ``False``.

        Raises:
            NotImplementedError: When ``connections_raises`` is ``True``
                (the default), to exercise the orchestrator's skip path.
        """
        if self._connections_raises:
            raise NotImplementedError("schema pending")
        return iter(())


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
        assert s.run("MATCH (p:Post) RETURN count(p) AS c").single()["c"] == 3  # type: ignore[index]
        assert s.run("MATCH (p:Posting) RETURN count(p) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (c:Comment) RETURN count(c) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (m:Message) RETURN count(m) AS c").single()["c"] == 1  # type: ignore[index]
        assert s.run("MATCH (:Comment)-[:ON]->(:Posting) RETURN count(*) AS c").single()["c"] == 1  # type: ignore[index]
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
        _FakeAdapter(connections_raises=True),
        migrated_driver,
        raw,
        load_retention_env(),
    )
    assert "connections" in result["skipped"]
    assert result["counts"]["connections"] == 0
