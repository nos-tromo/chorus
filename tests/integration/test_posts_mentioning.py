"""posts_mentioning tool: empty database returns no hits and writes one audit row."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_posts_mentioning_empty(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Empty database returns no hits and zero audit result count.

    Verifies the tool degrades gracefully when nothing in the graph
    matches the query, rather than raising.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="Berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.hits == []
    assert out.audit_result_count() == 0


def test_posts_mentioning_finds_seeded_post(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A seeded post + MENTIONS edge is found and reported by entity id.

    Seeds a posting that mentions an entity named ``Berlin`` and
    confirms the tool returns it with the entity id populated for
    audit propagation.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-1'})
              ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (p:Post:Posting {uuid: 'p-1'})
              ON CREATE SET p.text = 'hello berlin',
                            p.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(e)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-1"
    assert out.hits[0].entity_id == "ent-1"
    assert out.hits[0].matched_name == "Berlin"
    assert out.audit_entities() == ["ent-1"]


def test_posts_mentioning_finds_unresolved_alias(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An unresolved alias mention is returned without an entity id.

    Seeds the current ingestion shape — ``(:Post)-[:MENTIONS]->(:Alias)``
    without any ``:RESOLVED_TO`` edge — and verifies the tool still
    finds the post via a case-insensitive alias lookup.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a:Alias {surface_form: 'Berlin'})
            MERGE (p:Post:Posting {uuid: 'p-alias'})
              ON CREATE SET p.text = 'alias only mention',
                            p.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(a)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-alias"
    assert out.hits[0].entity_id is None
    assert out.hits[0].matched_name == "Berlin"
    assert out.audit_entities() == []


def test_posts_mentioning_time_window_excludes_entity_branch_match(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """Entity-branch hits outside the [from, to) window must be excluded.

    Regression test: an earlier revision of the Cypher mixed ``AND``
    and ``OR`` without grouping the OR branches, so the time-window
    predicates only applied to the Alias branch. Posts matched via
    the Entity branch were returned regardless of ``from_``/``to``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-old'})
              ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (p:Post:Posting {uuid: 'p-old'})
              ON CREATE SET p.text = 'old entity-branch mention',
                            p.timestamp = datetime('2026-01-01T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(e)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn.model_validate(
            {
                "entity": "berlin",
                "from": "2026-06-01T00:00:00+00:00",
                "to": "2026-07-01T00:00:00+00:00",
                "limit": 10,
            }
        ),
        user="test-user",
        audit=in_memory_audit,
    )

    assert out.hits == []


def test_posts_mentioning_finds_resolved_alias_by_canonical_name(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A canonical-name query resolves through Alias -> Entity when present.

    Seeds the graph shape expected after alias resolution lands and
    verifies the tool can still find a post whose ``:MENTIONS`` edge
    terminates at ``:Alias`` rather than directly at ``:Entity``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'})
              ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (a:Alias {surface_form: 'BER'})
            MERGE (a)-[:RESOLVED_TO]->(e)
            MERGE (p:Post:Posting {uuid: 'p-resolved'})
              ON CREATE SET p.text = 'resolved alias mention',
                            p.timestamp = datetime('2026-05-03T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(a)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-resolved"
    assert out.hits[0].entity_id == "ent-berlin"
    assert out.hits[0].matched_name == "Berlin"
    assert out.audit_entities() == ["ent-berlin"]


def test_audit_row_written(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """One audit row is written per tool call, with the resolved user.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
    """
    import sqlite3

    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="Nowhere"),
        user="alice",
        audit=in_memory_audit,
    )
    rows = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT user, tool_name, result_count, status FROM audit_log")
        .fetchall()
    )
    assert rows == [("alice", "posts_mentioning", 0, "ok")]
