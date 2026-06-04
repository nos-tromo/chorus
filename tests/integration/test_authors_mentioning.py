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


def test_authors_mentioning_time_window_excludes_entity_branch(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """Entity-branch mentions outside the [from, to) window exclude the author.

    Regression guard for the AND/OR precedence bug that bit posts_mentioning:
    an unparenthesised OR let the time predicates apply only to the Alias branch.
    """
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-old'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-old'})
              ON CREATE SET p.text = 'old entity-branch mention',
                            p.timestamp = datetime('2026-01-01T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(e)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn.model_validate(
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

    assert out.authors == []


def test_authors_mentioning_resolved_alias_by_canonical_name(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """A canonical-name query matches through Alias -> Entity and records the id."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'BER'})
            MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-resolved'})
              ON CREATE SET p.text = 'resolved alias mention',
                            p.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(al)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [("auth-a", 1)]
    assert out.audit_entities() == ["ent-berlin"]


def test_authors_mentioning_unresolved_alias(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An unresolved alias still matches the author; no entity id is recorded."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-alias'})
              ON CREATE SET p.text = 'alias only mention',
                            p.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(al)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [("auth-a", 1)]
    assert out.audit_entities() == []


def test_authors_mentioning_lockstep_with_posts_mentioning(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """authors_mentioning(X) returns exactly the authors behind posts_mentioning(X).

    Covers the matching-mirror guarantee and that comments (not just postings)
    count toward authorship.
    """
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'BER'})
            MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (al2:Alias {surface_form: 'Berlin'})
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.handle = 'a1'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.handle = 'a2'
            MERGE (p1:Post:Posting {uuid: 'p1'})
              ON CREATE SET p1.text = 'via entity', p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p2'})
              ON CREATE SET p2.text = 'via resolved alias', p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (p3:Post:Comment {uuid: 'p3'})
              ON CREATE SET p3.text = 'via unresolved alias', p3.timestamp = datetime('2026-05-03T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a1)-[:AUTHORED]->(p2)
            MERGE (a2)-[:AUTHORED]->(p3)
            MERGE (p1)-[:MENTIONS]->(e)
            MERGE (p2)-[:MENTIONS]->(al)
            MERGE (p3)-[:MENTIONS]->(al2)
            """
        )

    pm = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=500),
        user="test-user",
        audit=in_memory_audit,
    )
    pm_uuids = [h.uuid for h in pm.hits]

    with migrated_driver.session() as s:
        rec = s.run(
            """
            MATCH (au:Author)-[:AUTHORED]->(p:Post)
            WHERE p.uuid IN $uuids
            RETURN collect(DISTINCT au.id) AS ids
            """,
            uuids=pm_uuids,
        ).single()
    expected_author_ids = set(rec["ids"])

    am = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=500),
        user="test-user",
        audit=in_memory_audit,
    )
    am_author_ids = {a.author_id for a in am.authors}

    assert am_author_ids == expected_author_ids
    assert am_author_ids == {"a1", "a2"}  # non-empty, both surfaces and a comment


def test_authors_mentioning_does_not_merge_same_display_name(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """Two distinct authors sharing a display name are returned as two rows."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.display_name = 'Alex', a1.handle = 'alex1'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.display_name = 'Alex', a2.handle = 'alex2'
            MERGE (p1:Post:Posting {uuid: 'p1'})
              ON CREATE SET p1.text = 'b1', p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p2'})
              ON CREATE SET p2.text = 'b2', p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a2)-[:AUTHORED]->(p2)
            MERGE (p1)-[:MENTIONS]->(al)
            MERGE (p2)-[:MENTIONS]->(al)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert len(out.authors) == 2
    assert {a.author_id for a in out.authors} == {"a1", "a2"}
    assert all(a.display_name == "Alex" for a in out.authors)


def test_audit_row_written(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """One audit row is written per tool call, with the resolved user."""
    import sqlite3

    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="Nowhere"),
        user="alice",
        audit=in_memory_audit,
    )
    rows = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT user, tool_name, result_count, status FROM audit_log")
        .fetchall()
    )
    assert rows == [("alice", "authors_mentioning", 0, "ok")]
