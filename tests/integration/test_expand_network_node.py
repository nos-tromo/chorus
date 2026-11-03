"""Integration tests for the expand_network_node tool (Neo4j testcontainer)."""

from __future__ import annotations

import io

import pytest
from neo4j import Driver
from pydantic import ValidationError

from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor
from tests._audit import operation_events


def _seed(driver: Driver) -> None:
    """Seed the shared bipartite graph used by every test in this module.

    Two authors, three posts, one resolved topic ("glimmer initiative" ->
    ent-1) and one unresolved topic ("harbor works").
    """
    with driver.session() as s:
        s.run(
            """
            MERGE (a1:Author {id: 'auth-1'}) ON CREATE SET a1.handle = 'quietfjord'
            MERGE (a2:Author {id: 'auth-2'}) ON CREATE SET a2.handle = 'mossyriver'
            MERGE (p1:Post:Posting {uuid: 'post-1'}) MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (p2:Post:Posting {uuid: 'post-2'}) MERGE (a1)-[:AUTHORED]->(p2)
            MERGE (p3:Post:Posting {uuid: 'post-3'}) MERGE (a2)-[:AUTHORED]->(p3)
            MERGE (glimmer:Alias {surface_form: 'glimmer initiative'})
            MERGE (harbor:Alias  {surface_form: 'harbor works'})
            MERGE (e1:Entity {id: 'ent-1'}) ON CREATE SET e1.canonical_name = 'Glimmer Initiative', e1.type = 'ORG'
            MERGE (glimmer)-[:RESOLVED_TO]->(e1)
            MERGE (p1)-[:MENTIONS]->(glimmer)
            MERGE (p2)-[:MENTIONS]->(glimmer)
            MERGE (p3)-[:MENTIONS]->(glimmer)
            MERGE (p1)-[:MENTIONS]->(harbor)
            """
        )


def test_author_expansion_returns_their_topics(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """author:auth-1 expands to the topics they mention, resolved key included."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn, expand_network_node

    _seed(migrated_driver)
    out = expand_network_node(
        migrated_driver,
        ExpandNetworkNodeIn(node_id="author:auth-1", limit=50),
        audit=audit_ctx,
    )

    node_ids = {n.id for n in out.nodes}
    assert node_ids == {"topic:ent-1", "topic:harbor works"}
    assert all(e.source == "author:auth-1" for e in out.edges)
    weights = {e.target: e.weight for e in out.edges}
    assert weights == {"topic:ent-1": 2, "topic:harbor works": 1}
    assert out.truncated is False


def test_topic_expansion_returns_mentioning_authors(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """topic:ent-1 (resolved key) expands to the authors mentioning it."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn, expand_network_node

    _seed(migrated_driver)
    out = expand_network_node(
        migrated_driver,
        ExpandNetworkNodeIn(node_id="topic:ent-1", limit=50),
        audit=audit_ctx,
    )

    node_ids = {n.id for n in out.nodes}
    assert node_ids == {"author:auth-1", "author:auth-2"}
    labels = {n.id: n.label for n in out.nodes}
    assert labels == {"author:auth-1": "quietfjord", "author:auth-2": "mossyriver"}
    assert all(e.target == "topic:ent-1" for e in out.edges)
    weights = {e.source: e.weight for e in out.edges}
    assert weights == {"author:auth-1": 2, "author:auth-2": 1}
    assert out.truncated is False


def test_unresolved_topic_key_is_surface_form(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """A topic with no RESOLVED_TO is matched by its alias surface form."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn, expand_network_node

    _seed(migrated_driver)
    out = expand_network_node(
        migrated_driver,
        ExpandNetworkNodeIn(node_id="topic:harbor works", limit=50),
        audit=audit_ctx,
    )

    node_ids = {n.id for n in out.nodes}
    assert node_ids == {"author:auth-1"}


def test_limit_truncates_deterministically(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """``limit`` caps neighbours by weight desc, deterministically, and flags truncated."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn, expand_network_node

    _seed(migrated_driver)
    out = expand_network_node(
        migrated_driver,
        ExpandNetworkNodeIn(node_id="topic:ent-1", limit=1),
        audit=audit_ctx,
    )

    assert len(out.nodes) == 1
    assert out.nodes[0].id == "author:auth-1"
    assert out.truncated is True


def test_unknown_node_yields_empty(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """Expanding a node id matching nothing returns an empty, non-truncated result."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn, expand_network_node

    _seed(migrated_driver)
    out = expand_network_node(
        migrated_driver,
        ExpandNetworkNodeIn(node_id="author:no-such-author"),
        audit=audit_ctx,
    )

    assert out.nodes == []
    assert out.edges == []
    assert out.truncated is False


def test_bad_namespace_rejected() -> None:
    """A node_id without a known namespace prefix fails input validation."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn

    with pytest.raises(ValidationError):
        ExpandNetworkNodeIn(node_id="banana")


def test_audit_event_written(migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """One query event per call: the clicked author and the resolved topic, by id."""
    from chorus.tools.expand_network_node import ExpandNetworkNodeIn, expand_network_node

    _seed(migrated_driver)
    out = expand_network_node(
        migrated_driver,
        ExpandNetworkNodeIn(node_id="author:auth-1", limit=50),
        audit=audit_trail.context(Actor(user="alice", via="edge")),
    )
    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "tool.expand_network_node", "ok")
    assert event["criteria"] == {"node_id": "author:auth-1", "limit": 50}
    assert event["target"] == {"kind": "mixed", "ids": ["author:auth-1", "entity:ent-1"], "count": len(out.nodes)}
