"""authors_mentioning tool: ranks authors who mention an entity."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_authors_mentioning_empty(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Empty database returns no authors and zero audit result count."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="Berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.authors == []
    assert out.audit_result_count() == 0


def test_authors_mentioning_ranks_by_post_count(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Authors are ranked by how many of their posts mention the entity."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (al2:Alias {surface_form: 'Munich'})
            MERGE (a:Author {id: 'auth-a'})
              ON CREATE SET a.handle = 'a', a.display_name = 'Anna', a.platform = 'x'
            MERGE (b:Author {id: 'auth-b'})
              ON CREATE SET b.handle = 'b', b.display_name = 'Bob', b.platform = 'x'
            MERGE (c:Author {id: 'auth-c'}) ON CREATE SET c.handle = 'c'
            MERGE (pa1:Post:Posting {uuid: 'pa1'})
              ON CREATE SET pa1.text = 'berlin one', pa1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (pa2:Post:Posting {uuid: 'pa2'})
              ON CREATE SET pa2.text = 'berlin two', pa2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (pb1:Post:Posting {uuid: 'pb1'})
              ON CREATE SET pb1.text = 'berlin three', pb1.timestamp = datetime('2026-05-03T10:00:00+00:00')
            MERGE (pc1:Post:Posting {uuid: 'pc1'})
              ON CREATE SET pc1.text = 'munich', pc1.timestamp = datetime('2026-05-04T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(pa1)
            MERGE (a)-[:AUTHORED]->(pa2)
            MERGE (b)-[:AUTHORED]->(pb1)
            MERGE (c)-[:AUTHORED]->(pc1)
            MERGE (pa1)-[:MENTIONS]->(al)
            MERGE (pa2)-[:MENTIONS]->(al)
            MERGE (pb1)-[:MENTIONS]->(al)
            MERGE (pc1)-[:MENTIONS]->(al2)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [
        ("auth-a", 2),
        ("auth-b", 1),
    ]
    assert out.authors[0].display_name == "Anna"
    assert out.authors[0].first_mention.isoformat() == "2026-05-01T10:00:00+00:00"
    assert out.authors[0].last_mention.isoformat() == "2026-05-02T10:00:00+00:00"


def test_authors_mentioning_counts_distinct_posts(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A post that matches the query via two aliases counts once, not twice."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (a1:Alias {surface_form: 'Berlin'})
            MERGE (a2:Alias {surface_form: 'BER'})
            MERGE (a2)-[:RESOLVED_TO]->(e)
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-multi'})
              ON CREATE SET p.text = 'Berlin a.k.a. BER',
                            p.timestamp = datetime('2026-05-05T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(a1)
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [("auth-a", 1)]
