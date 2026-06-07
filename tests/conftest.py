"""Shared pytest fixtures.

`neo4j_url` spins up a single Neo4j 5.26.26 container per session — the
vector-index features chorus uses landed in 5.11+, and the community
image is sufficient. The fixture sets the chorus env vars and reloads
the modules that snapshot them at import time, so each test sees a fresh
driver wired to the testcontainer.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from neo4j import Driver
from testcontainers.neo4j import Neo4jContainer

_CHORUS_ENV_MODULES = (
    "chorus.utils.env_cfg",
    "chorus.utils.logger_cfg",
    "chorus.db.neo4j",
    "chorus.audit.logger",
    "chorus.api.auth.principal",
    "chorus.api.routers.health",
    "chorus.api.routers.tools",
    "chorus.api.routers.agent",
    "chorus.api.routers.ingestion",
    "chorus.tools._template_loader",
    "chorus.tools._audit",
    "chorus.tools.posts_mentioning",
    "chorus.tools.author_activity_summary",
    "chorus.tools.topic_co_occurrence",
    "chorus.tools.authors_connected_by_topic",
    "chorus.tools.authors_mentioning",
    "chorus.tools.network_around",
    "chorus.tools",
    "chorus.agent.prompts",
    "chorus.agent.openai_tools",
    "chorus.agent.loop",
    "chorus.agent",
    "chorus.inference.ner_client",
    "chorus.ingestion.jobs",
    "chorus.ingestion.raw_store",
    "chorus.ingestion.postings",
    "chorus.ingestion.comments",
    "chorus.ingestion.messages",
    "chorus.ingestion.profiles",
    "chorus.ingestion.connections",
    "chorus.ingestion.extraction",
    "chorus.ingestion.resolution",
    "chorus.ingestion.orchestrator",
    "chorus.api.main",
)


def _reload_chorus() -> None:
    """Evict chorus modules from ``sys.modules`` so env vars are re-read.

    Several chorus modules snapshot environment variables at import
    time (via ``lru_cache`` or module-level dataclass loaders). Tests
    that need to override those env vars must reset the import cache
    first; this helper does that for the set listed in
    :data:`_CHORUS_ENV_MODULES`.
    """
    for mod in _CHORUS_ENV_MODULES:
        if mod in sys.modules:
            del sys.modules[mod]


@pytest.fixture(scope="session")
def neo4j_container() -> Iterator[tuple[str, str, str]]:
    """Session-scoped Neo4j 5.26.26 testcontainer.

    Boots a single container for the whole test session because
    spinning up Neo4j is slow. Per-test cleanup is handled by the
    :func:`driver` fixture, not by recycling the container.

    Yields:
        ``(bolt_uri, username, password)`` for the container.
    """
    with Neo4jContainer("neo4j:5.26.26-community") as c:
        yield c.get_connection_url(), "neo4j", c.password


@pytest.fixture
def chorus_env(neo4j_container: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every chorus env var at the testcontainer + a temp home.

    Sets ``NEO4J_*``, ``CHORUS_HOME``, and ``CHORUS_DEFAULT_IDENTITY``
    via ``monkeypatch``, then evicts the chorus module cache so the
    new values are picked up.

    Args:
        neo4j_container: Session-scoped Neo4j container tuple.
        monkeypatch: pytest monkeypatch fixture (used to scope env
            changes to this test).

    Returns:
        Path to the temporary ``CHORUS_HOME`` directory.
    """
    uri, user, password = neo4j_container
    td = Path(tempfile.mkdtemp(prefix="chorus-test-"))
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.setenv("NEO4J_USER", user)
    monkeypatch.setenv("NEO4J_PASSWORD", password)
    monkeypatch.setenv("CHORUS_HOME", str(td))
    monkeypatch.setenv("CHORUS_DEFAULT_IDENTITY", "test-user")
    _reload_chorus()
    return td


@pytest.fixture
def driver(chorus_env: Path) -> Iterator[Driver]:
    """Open Neo4j driver wired to a freshly-cleaned database.

    Wipes all nodes and drops user-defined indexes and constraints so
    each test starts from a clean slate. The driver is closed at
    teardown so the next fixture instance can rebuild it.

    Args:
        chorus_env: The :func:`chorus_env` fixture (ensures env vars
            point at the testcontainer).

    Yields:
        An open :class:`neo4j.Driver` against the cleaned database.
    """
    from chorus.db.neo4j import close_driver, get_driver

    d = get_driver()
    # Wipe data + drop user-defined indexes/constraints so each test starts
    # from a clean slate.
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for record in s.run("SHOW CONSTRAINTS YIELD name").data():
            s.run(f"DROP CONSTRAINT {record['name']} IF EXISTS").consume()
        for record in s.run("SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP'").data():
            s.run(f"DROP INDEX {record['name']} IF EXISTS").consume()
    yield d
    close_driver()


@pytest.fixture
def migrated_driver(driver: Driver) -> Driver:
    """Driver against a database that has had all migrations applied.

    Args:
        driver: The clean :func:`driver` fixture.

    Returns:
        The same driver after :func:`apply_all` has been run.
    """
    from chorus.migrations.runner import apply_all

    apply_all(driver)
    return driver


@pytest.fixture
def fake_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the inference provider and NER client for unit tests.

    Replaces ``chat``, ``embed``, and ``rerank`` on
    :mod:`chorus.inference.provider` plus ``extract_entities`` on
    :mod:`chorus.inference.ner_client` with deterministic stand-ins so
    nothing reaches the LiteLLM proxy or the GLiNER service.

    Args:
        monkeypatch: pytest monkeypatch fixture (used to scope the
            patches to this test).
    """
    from chorus.inference import ner_client, provider

    monkeypatch.setattr(provider, "chat", lambda *a, **kw: "stub")
    monkeypatch.setattr(
        provider,
        "embed",
        lambda texts, **kw: [[0.0] * 1024 for _ in texts],
    )
    monkeypatch.setattr(
        provider,
        "rerank",
        lambda query, docs, **kw: [(i, 1.0 - i * 0.01) for i in range(len(docs))],
    )
    monkeypatch.setattr(
        ner_client,
        "extract_entities",
        lambda text, **kw: [],
    )


@pytest.fixture
def in_memory_audit(chorus_env: Path) -> Any:
    """Return a fresh :class:`AuditLogger` backed by a temp SQLite file.

    For tools that want a real audit logger rather than a stub.

    Args:
        chorus_env: Provides ``CHORUS_HOME`` for the temp DB location.

    Returns:
        A :class:`chorus.audit.logger.AuditLogger` whose schema has
        already been applied.
    """
    from chorus.audit.logger import AuditLogger

    a = AuditLogger(chorus_env / "audit.sqlite")
    a.init_schema()
    return a
