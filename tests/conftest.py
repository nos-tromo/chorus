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
    "chorus.tools._template_loader",
    "chorus.tools._audit",
    "chorus.tools.posts_mentioning",
    "chorus.tools",
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
    for mod in _CHORUS_ENV_MODULES:
        if mod in sys.modules:
            del sys.modules[mod]


@pytest.fixture(scope="session")
def neo4j_container() -> Iterator[tuple[str, str, str]]:
    with Neo4jContainer("neo4j:5.26.26-community") as c:
        yield c.get_connection_url(), "neo4j", c.password


@pytest.fixture
def chorus_env(
    neo4j_container: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> Path:
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
    from chorus.db.neo4j import close_driver, get_driver

    d = get_driver()
    # Wipe data + drop user-defined indexes/constraints so each test starts
    # from a clean slate.
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for record in s.run("SHOW CONSTRAINTS YIELD name").data():
            s.run(f"DROP CONSTRAINT {record['name']} IF EXISTS").consume()
        for record in s.run(
            "SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP'"
        ).data():
            s.run(f"DROP INDEX {record['name']} IF EXISTS").consume()
    yield d
    close_driver()


@pytest.fixture
def migrated_driver(driver: Driver) -> Driver:
    from chorus.migrations.runner import apply_all

    apply_all(driver)
    return driver


@pytest.fixture
def fake_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the inference provider so unit tests don't reach the proxy."""
    from chorus.inference import provider

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
        provider,
        "extract_entities",
        lambda text, **kw: [],
    )


@pytest.fixture
def in_memory_audit(chorus_env: Path) -> Any:
    """An AuditLogger over a temp DB, for tools that want a real one."""
    from chorus.audit.logger import AuditLogger

    a = AuditLogger(chorus_env / "audit.sqlite")
    a.init_schema()
    return a
