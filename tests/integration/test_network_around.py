"""network_around: the bipartite Author<->Topic ego network around a seed topic."""

from __future__ import annotations

from typing import Any

import pytest
from neo4j import Driver
from pydantic import ValidationError


def _ids(nodes: list[Any]) -> set[str]:
    """Return the set of node ids from a list of NetworkNode."""
    return {n.id for n in nodes}


def test_depth_above_two_rejected() -> None:
    """``depth`` > 2 is not yet supported and fails input validation (-> 422)."""
    from chorus.tools.network_around import NetworkAroundIn

    with pytest.raises(ValidationError):
        NetworkAroundIn(entity="Berlin", depth=3)


def test_empty_seed_returns_empty_network(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A seed matching nothing yields an empty network with no seed node."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="Nowhere", depth=2),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.nodes == []
    assert out.edges == []
    assert out.seed_node_id is None
    assert out.truncated is False
    assert out.audit_result_count() == 0


def test_depth_one_star(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """depth=1 returns the seed topic plus authors, every edge author -> seed."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (paris:Alias  {surface_form: 'Paris'})
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'anna'
            MERGE (b:Author {id: 'b'}) ON CREATE SET b.handle = 'bob'
            MERGE (pa1:Post:Posting {uuid: 'pa1'}) MERGE (a)-[:AUTHORED]->(pa1)
            MERGE (pa2:Post:Posting {uuid: 'pa2'}) MERGE (a)-[:AUTHORED]->(pa2)
            MERGE (pb1:Post:Posting {uuid: 'pb1'}) MERGE (b)-[:AUTHORED]->(pb1)
            MERGE (pa1)-[:MENTIONS]->(berlin)
            MERGE (pa2)-[:MENTIONS]->(berlin)
            MERGE (pb1)-[:MENTIONS]->(berlin)
            MERGE (pa1)-[:MENTIONS]->(paris)
            """
        )
    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1),
        user="test-user",
        audit=in_memory_audit,
    )

    assert out.seed_node_id == "topic:Berlin"
    topics = [n for n in out.nodes if n.kind == "topic"]
    assert [n.id for n in topics] == ["topic:Berlin"]  # no co-topics at depth 1
    assert {n.id for n in out.nodes if n.kind == "author"} == {"author:a", "author:b"}
    # every edge points at the seed; A mentions Berlin in two posts, B in one
    assert all(e.target == "topic:Berlin" for e in out.edges)
    weights = {e.source: e.weight for e in out.edges}
    assert weights == {"author:a": 2, "author:b": 1}


def test_depth_one_lockstep_with_authors_mentioning(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """The depth-1 author set equals authors_mentioning(X)."""
    from chorus.tools.authors_mentioning import AuthorsMentioningIn, authors_mentioning
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'BER'}) MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (al2:Alias {surface_form: 'Berlin'})
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.handle = 'a1'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.handle = 'a2'
            MERGE (p1:Post:Posting {uuid: 'p1'})
              ON CREATE SET p1.text = 'via entity', p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Comment {uuid: 'p2'})
              ON CREATE SET p2.text = 'via alias', p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a2)-[:AUTHORED]->(p2)
            MERGE (p1)-[:MENTIONS]->(e)
            MERGE (p2)-[:MENTIONS]->(al2)
            """
        )
    na = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1),
        user="test-user",
        audit=in_memory_audit,
    )
    am = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=500),
        user="test-user",
        audit=in_memory_audit,
    )

    na_authors = {n.id.removeprefix("author:") for n in na.nodes if n.kind == "author"}
    am_authors = {a.author_id for a in am.authors}
    assert na_authors == am_authors == {"a1", "a2"}


def test_depth_two_expansion(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """depth=2 adds the co-topics of the ring-1 authors, keeping the seed star."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (paris:Alias  {surface_form: 'Paris'})
            MERGE (rome:Alias   {surface_form: 'Rome'})
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'anna'
            MERGE (pa1:Post:Posting {uuid: 'pa1'}) MERGE (a)-[:AUTHORED]->(pa1)
            MERGE (pa2:Post:Posting {uuid: 'pa2'}) MERGE (a)-[:AUTHORED]->(pa2)
            MERGE (pa1)-[:MENTIONS]->(berlin)
            MERGE (pa1)-[:MENTIONS]->(paris)
            MERGE (pa2)-[:MENTIONS]->(rome)
            """
        )
    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=2),
        user="test-user",
        audit=in_memory_audit,
    )

    topic_ids = {n.id for n in out.nodes if n.kind == "topic"}
    assert topic_ids == {"topic:Berlin", "topic:Paris", "topic:Rome"}
    # seed star edge still present (author -> seed), plus the two co-topics
    assert ("author:a", "topic:Berlin") in {(e.source, e.target) for e in out.edges}
    assert {(e.source, e.target) for e in out.edges} == {
        ("author:a", "topic:Berlin"),
        ("author:a", "topic:Paris"),
        ("author:a", "topic:Rome"),
    }


def test_resolved_and_unresolved_both_match(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Seeding by canonical name and by surface form both build the network.

    The resolved seed topic carries its entity id (and audit surfaces it); an
    unresolved co-topic carries none.
    """
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'BER'}) MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (paris:Alias {surface_form: 'Paris'})
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'anna'
            MERGE (p1:Post:Posting {uuid: 'p1'}) MERGE (a)-[:AUTHORED]->(p1)
            MERGE (p1)-[:MENTIONS]->(al)
            MERGE (p1)-[:MENTIONS]->(paris)
            """
        )
    # seed by canonical name
    by_name = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=2),
        user="test-user",
        audit=in_memory_audit,
    )
    # seed by surface form
    by_form = network_around(
        migrated_driver,
        NetworkAroundIn(entity="BER", depth=2),
        user="test-user",
        audit=in_memory_audit,
    )

    for out in (by_name, by_form):
        assert out.seed_node_id == "topic:ent-berlin"
        seed_node = next(n for n in out.nodes if n.is_seed)
        assert seed_node.entity_id == "ent-berlin"
        assert seed_node.label == "Berlin"
        paris = next(n for n in out.nodes if n.id == "topic:Paris")
        assert paris.entity_id is None
        assert out.audit_entities() == ["ent-berlin"]


def test_unresolved_alias_seed_has_no_audit_entity(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An alias-only seed builds the network but surfaces no audit entity id."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'anna'
            MERGE (p1:Post:Posting {uuid: 'p1'}) MERGE (a)-[:AUTHORED]->(p1)
            MERGE (p1)-[:MENTIONS]->(berlin)
            """
        )
    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.seed_node_id == "topic:Berlin"
    assert out.audit_entities() == []


def test_limit_caps_author_ring(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """``limit`` caps the author ring deterministically and flips truncated."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            WITH berlin
            UNWIND range(1, 5) AS i
            MERGE (a:Author {id: 'a' + toString(i)})
            MERGE (p:Post:Posting {uuid: 'p' + toString(i)})
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(berlin)
            """
        )
    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1, limit=2),
        user="test-user",
        audit=in_memory_audit,
    )
    authors = [n for n in out.nodes if n.kind == "author"]
    assert len(authors) == 2
    assert out.truncated is True

    full = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1, limit=50),
        user="test-user",
        audit=in_memory_audit,
    )
    assert len([n for n in full.nodes if n.kind == "author"]) == 5
    assert full.truncated is False


def test_topic_limit_caps_second_ring(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """``topic_limit`` caps second-ring topics by weight, always keeping the seed."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    # Author mentions Berlin (seed) once, then three co-topics with descending weight.
    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (heavy:Alias  {surface_form: 'Heavy'})
            MERGE (mid:Alias    {surface_form: 'Mid'})
            MERGE (light:Alias  {surface_form: 'Light'})
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'anna'
            MERGE (seedpost:Post:Posting {uuid: 'seedpost'}) MERGE (a)-[:AUTHORED]->(seedpost)
            MERGE (seedpost)-[:MENTIONS]->(berlin)
            WITH a, heavy, mid, light, seedpost
            UNWIND range(1, 3) AS i
            MERGE (ph:Post:Posting {uuid: 'ph' + toString(i)}) MERGE (a)-[:AUTHORED]->(ph)
            MERGE (ph)-[:MENTIONS]->(heavy)
            WITH a, mid, light
            UNWIND range(1, 2) AS j
            MERGE (pm:Post:Posting {uuid: 'pm' + toString(j)}) MERGE (a)-[:AUTHORED]->(pm)
            MERGE (pm)-[:MENTIONS]->(mid)
            WITH a, light
            MERGE (pl:Post:Posting {uuid: 'pl1'}) MERGE (a)-[:AUTHORED]->(pl)
            MERGE (pl)-[:MENTIONS]->(light)
            """
        )
    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=2, topic_limit=1),
        user="test-user",
        audit=in_memory_audit,
    )
    topic_ids = {n.id for n in out.nodes if n.kind == "topic"}
    # seed always kept + the single heaviest co-topic (Heavy, weight 3)
    assert topic_ids == {"topic:Berlin", "topic:Heavy"}
    assert out.truncated is True


def test_does_not_merge_same_display_name(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Two distinct authors sharing a display name are two author nodes."""
    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.display_name = 'Alex'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.display_name = 'Alex'
            MERGE (p1:Post:Posting {uuid: 'p1'}) MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (p2:Post:Posting {uuid: 'p2'}) MERGE (a2)-[:AUTHORED]->(p2)
            MERGE (p1)-[:MENTIONS]->(berlin)
            MERGE (p2)-[:MENTIONS]->(berlin)
            """
        )
    out = network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1),
        user="test-user",
        audit=in_memory_audit,
    )
    authors = {n.id for n in out.nodes if n.kind == "author"}
    assert authors == {"author:a1", "author:a2"}


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "network_around" in TOOLS


def test_audit_row_written(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """One audit row is written per tool call, with node count as result_count."""
    import sqlite3

    from chorus.tools.network_around import NetworkAroundIn, network_around

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (a:Author {id: 'a'}) ON CREATE SET a.handle = 'anna'
            MERGE (p1:Post:Posting {uuid: 'p1'}) MERGE (a)-[:AUTHORED]->(p1)
            MERGE (p1)-[:MENTIONS]->(berlin)
            """
        )
    network_around(
        migrated_driver,
        NetworkAroundIn(entity="berlin", depth=1),
        user="alice",
        audit=in_memory_audit,
    )
    rows = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT user, tool_name, result_count, status FROM audit_log")
        .fetchall()
    )
    # seed topic + one author = 2 nodes
    assert rows == [("alice", "network_around", 2, "ok")]
