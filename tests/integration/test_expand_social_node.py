"""expand_social_node: one-hop expansion of the social graph seeded by :Author.id."""

from __future__ import annotations

import io
from typing import Any

import pytest
from neo4j import Driver
from pydantic import ValidationError

from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor
from tests._audit import operation_events


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


def test_expansion_returns_direct_ties(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """Expanding auth-a returns both neighbours and the three connecting edges."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    _seed_social_graph(migrated_driver)
    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="auth-a", limit=50),
        audit=audit_ctx,
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


def test_limit_truncates_by_degree(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """limit=1 keeps only auth-b (degree 2) over auth-c (degree 1); truncated flips True."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    _seed_social_graph(migrated_driver)
    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="auth-a", limit=1),
        audit=audit_ctx,
    )

    assert _node_ids(out) == {"author:auth-b"}
    assert out.truncated is True


def test_unknown_author_yields_empty(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """An author id matching nothing yields an empty expansion, not an error."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="no-such", limit=50),
        audit=audit_ctx,
    )

    assert out.nodes == []
    assert out.edges == []
    assert out.truncated is False


def test_empty_author_id_rejected() -> None:
    """A blank/whitespace-only author_id fails input validation (-> 422)."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn

    with pytest.raises(ValidationError):
        ExpandSocialNodeIn(author_id="   ")


def test_audit_records_author_ids(migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """The event records the clicked author plus both neighbours; the count is the neighbour count."""
    from chorus.tools.expand_social_node import ExpandSocialNodeIn, expand_social_node

    _seed_social_graph(migrated_driver)
    out = expand_social_node(
        migrated_driver,
        ExpandSocialNodeIn(author_id="auth-a", limit=50),
        audit=audit_trail.context(Actor(user="alice", via="edge")),
    )

    assert set(out.audit_authors()) == {"auth-a", "auth-b", "auth-c"}
    assert out.audit_result_count() == 2

    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "tool.expand_social_node", "ok")
    assert event["actor"] == {"user": "alice", "via": "edge"}
    assert event["target"]["kind"] == "mixed"
    assert event["target"]["count"] == 2
    assert event["target"]["ids"][0] == "author:auth-a"
    assert set(event["target"]["ids"]) == {"author:auth-a", "author:auth-b", "author:auth-c"}


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "expand_social_node" in TOOLS
