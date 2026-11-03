"""Shared pytest fixtures.

`neo4j_url` spins up a single Neo4j 5.26.26 container per session — the
vector-index features chorus uses landed in 5.11+, and the community
image is sufficient. The fixture sets the chorus env vars and reloads
the modules that snapshot them at import time, so each test sees a fresh
driver wired to the testcontainer.
"""

from __future__ import annotations

import io
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from neo4j import Driver
from testcontainers.neo4j import Neo4jContainer

from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor, AuditEmitter
from tests._audit import assert_chain_ok

_CHORUS_ENV_MODULES = (
    "chorus.utils.env_cfg",
    "chorus.utils.logger_cfg",
    "chorus.db.neo4j",
    "chorus.api.auth.principal",
    # NOTE: chorus.audit.trail and chorus.audit.cli are deliberately NOT
    # reloaded: neither snapshots env at import, and AuditFailure /
    # PurposeRequired must keep one class identity for every `except` that
    # catches them.
    "chorus.api.audit",
    "chorus.api.routers.health",
    "chorus.api.routers.tools",
    "chorus.api.routers.agent",
    "chorus.api.routers.ingestion",
    "chorus.api.routers.config",
    "chorus.api.routers.stats",
    "chorus.api.routers.whoami",
    "chorus.tools._template_loader",
    "chorus.tools._audit",
    "chorus.tools.posts_mentioning",
    "chorus.tools.author_activity_summary",
    "chorus.tools.topic_co_occurrence",
    "chorus.tools.authors_connected_by_topic",
    "chorus.tools.authors_mentioning",
    "chorus.tools.expand_network_node",
    "chorus.tools.expand_social_node",
    "chorus.tools.network_around",
    "chorus.tools.social_network_around",
    "chorus.tools",
    "chorus.agent.prompts",
    "chorus.agent.openai_tools",
    "chorus.agent.loop",
    "chorus.agent",
    "chorus.inference.ner_client",
    # NOTE: chorus.ingestion.jobs is deliberately NOT reloaded. It snapshots no
    # env, and reloading it would mint a second JobBusyError/JobRegistry class
    # identity that the test module's top-level import wouldn't share — making
    # the router's `except JobBusyError` silently miss. Keep it singular.
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


@pytest.fixture(autouse=True)
def _no_edge_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``CHORUS_EDGE_SECRET`` out of every test that does not set it.

    Most tests send a bare ``X-Auth-User``, which a configured secret would
    ignore. Set empty rather than deleted: ``_env`` reads empty as unset,
    while a deleted variable comes back from a local ``.env`` when a module
    reload re-runs ``load_dotenv``.

    Args:
        monkeypatch: pytest monkeypatch fixture (scopes the change to the test).
    """
    monkeypatch.setenv("CHORUS_EDGE_SECRET", "")


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

    The migrations are recorded on a throwaway audit chain of their own, so
    they never show up in a test's ``audit_stream``.

    Args:
        driver: The clean :func:`driver` fixture.

    Returns:
        The same driver after :func:`apply_all` has been run.
    """
    from chorus.migrations.runner import apply_all

    stream = io.StringIO()
    trail = AuditTrail(AuditEmitter("chorus", "test", stream=stream, heartbeat_interval_s=0))
    trail.start({})
    apply_all(driver, audit=trail.system_context())
    trail.close()
    assert_chain_ok(stream)
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
def audit_stream() -> io.StringIO:
    """Capture buffer for the test's audit chain.

    Returns:
        An empty ``io.StringIO`` the :func:`audit_trail` emitter writes to.
    """
    return io.StringIO()


@pytest.fixture
def audit_trail(audit_stream: io.StringIO) -> Iterator[AuditTrail]:
    """A started audit trail writing to :func:`audit_stream`, no heartbeat.

    At teardown the chain is closed and verified with ``verify_chain()``, so
    every test that emits through it also checks the chain it wrote.

    Args:
        audit_stream: The capture buffer.

    Yields:
        The started :class:`~chorus.audit.trail.AuditTrail`.
    """
    trail = AuditTrail(AuditEmitter("chorus", "test", stream=audit_stream, heartbeat_interval_s=0))
    trail.start({"edge_secret_enforced": False, "default_identity_enabled": True})
    yield trail
    trail.close()
    assert_chain_ok(audit_stream)


@pytest.fixture
def audit_ctx(audit_trail: AuditTrail) -> AuditContext:
    """The audit trail bound to a synthetic dev caller.

    Args:
        audit_trail: The test's audit trail.

    Returns:
        An :class:`~chorus.audit.trail.AuditContext` for
        ``Actor("test-user", "system")`` with no stated purpose.
    """
    return audit_trail.context(Actor(user="test-user", via="system"))
