"""expand_social_node: one-hop expansion of the social graph seeded by :Author.id."""

from __future__ import annotations

from typing import Any

import pytest
from neo4j import Driver
from pydantic import ValidationError


def _edge_tuples(out: Any) -> set[tuple[str, str, str, bool]]:
    """Return each edge as a ``(source, target, kind, directed)`` tuple."""
    return {(e.source, e.target, e.kind, e.directed) for e in out.edges}


def _node_ids(out: Any) -> set[str]:
    """Return the set of node ids in the returned expansion."""
    return {n.id for n in out.nodes}


def _seed_social_graph(driver: Driver) -> None:
    """Seed the synthetic three-author fixture shared by all tests in this module.

    Authors: ``auth-a`` (handle ``sablecliff``), ``auth-b`` (handle
    ``fernhollow``), ``auth-c`` (handle ``driftgate``). Edges: ``auth-b``
    follows ``auth-a``, ``auth-a`` follows ``auth-c``, and ``auth-a`` is
    friends with ``auth-b`` (stored canonically ``auth-a`` -> ``auth-b``,
    the lower-id direction).
    """
    with driver.session() as s:
        s.run(
            """
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'sablecliff'
            MERGE (b:Author {id: 'auth-b'}) ON CREATE SET b.handle = 'fernhollow'
            MERGE (c:Author {id: 'auth-c'}) ON CREATE SET c.handle = 'driftgate'
            MERGE (b)-[:FOLLOWS]->(a)
            MERGE (a)-[:FOLLOWS]->(c)
            MERGE (a)-[:FRIENDS_WITH]->(b)
            """
        )


def test_expansion_returns_direct_ties(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Expanding auth-a returns both neighbours and the three connecting edges."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    _seed_social_graph(migrated_driver)
    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="auth-a", limit=50),
        user="test-user",
        audit=in_memory_audit,
    )

    assert _node_ids(out) == {"author:auth-b", "author:auth-c"}
    assert next(n for n in out.nodes if n.id == "author:auth-b").label == "fernhollow"
    assert next(n for n in out.nodes if n.id == "author:auth-c").label == "driftgate"
    assert _edge_tuples(out) == {
        ("author:auth-b", "author:auth-a", "follows", True),
        ("author:auth-a", "author:auth-c", "follows", True),
        ("author:auth-a", "author:auth-b", "friends", False),
    }
    assert out.truncated is False


def test_limit_truncates_by_degree(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """limit=1 keeps only auth-b (degree 2) over auth-c (degree 1); truncated flips True."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    _seed_social_graph(migrated_driver)
    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="auth-a", limit=1),
        user="test-user",
        audit=in_memory_audit,
    )

    assert _node_ids(out) == {"author:auth-b"}
    assert out.truncated is True


def test_unknown_author_yields_empty(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An author id matching nothing yields an empty expansion, not an error."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="no-such", limit=50),
        user="test-user",
        audit=in_memory_audit,
    )

    assert out.nodes == []
    assert out.edges == []
    assert out.truncated is False


def test_empty_author_id_rejected() -> None:
    """A blank/whitespace-only author_id fails input validation (-> 422)."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn

    with pytest.raises(ValidationError):
        ExpandSocialNodeIn(author_id="   ")


def test_audit_records_author_ids(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Audit records the clicked author plus both neighbours; result_count is neighbour count."""
    import json
    import sqlite3

    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    _seed_social_graph(migrated_driver)
    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="auth-a", limit=50),
        user="alice",
        audit=in_memory_audit,
    )

    assert set(out.audit_entities()) == {"auth-a", "auth-b", "auth-c"}
    assert out.audit_result_count() == 2

    rows = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT user, tool_name, entities_touched_json, result_count, status FROM audit_log")
        .fetchall()
    )
    assert len(rows) == 1
    user, tool_name, entities_json, result_count, status = rows[0]
    assert (user, tool_name, result_count, status) == ("alice", "expand_social_node", 2, "ok")
    assert set(json.loads(entities_json)) == {"auth-a", "auth-b", "auth-c"}


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "expand_social_node" in TOOLS
