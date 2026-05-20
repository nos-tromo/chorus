"""posts_mentioning tool: empty database returns no hits and writes one audit row."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_posts_mentioning_empty(migrated_driver: Driver, in_memory_audit: Any) -> None:
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


def test_posts_mentioning_finds_seeded_post(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
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
        PostsMentioningIn(entity="Berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-1"
    assert out.hits[0].entity_id == "ent-1"
    assert out.audit_entities() == ["ent-1"]


def test_audit_row_written(migrated_driver: Driver, in_memory_audit: Any) -> None:
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
