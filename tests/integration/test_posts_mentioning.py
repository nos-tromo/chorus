"""posts_mentioning tool: empty database returns no hits and writes one audit row."""

from __future__ import annotations

import io

from neo4j import Driver

from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor
from tests._audit import operation_events


def test_posts_mentioning_empty(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """Empty database returns no hits and zero audit result count.

    Verifies the tool degrades gracefully when nothing in the graph
    matches the query, rather than raising.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="Berlin", limit=10),
        audit=audit_ctx,
    )
    assert out.hits == []
    assert out.audit_result_count() == 0


def test_posts_mentioning_finds_seeded_post(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """A seeded post + MENTIONS edge is found and reported by entity id.

    Seeds a posting that mentions an entity named ``Berlin`` and
    confirms the tool returns it with the entity id populated for
    audit propagation.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
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
        audit=audit_ctx,
    )
    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-1"
    assert out.hits[0].entity_id == "ent-1"
    assert out.hits[0].matched_name == "Berlin"
    assert out.audit_entities() == ["ent-1"]


def test_posts_mentioning_finds_unresolved_alias(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """An unresolved alias mention is returned without an entity id.

    Seeds the current ingestion shape — ``(:Post)-[:MENTIONS]->(:Alias)``
    without any ``:RESOLVED_TO`` edge — and verifies the tool still
    finds the post via a case-insensitive alias lookup.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
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
        audit=audit_ctx,
    )

    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-alias"
    assert out.hits[0].entity_id is None
    assert out.hits[0].matched_name == "Berlin"
    assert out.audit_entities() == []


def test_posts_mentioning_ignores_incomplete_post_stubs(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """Thin :Post stubs with missing text/timestamp must not crash the tool.

    Comment and message ingestion can create placeholder parent ``:Post``
    nodes before the full row arrives. If malformed data also attaches a
    ``:MENTIONS`` edge to such a stub, the tool should ignore it and still
    return any fully-materialized matches.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a:Alias {surface_form: 'Deutschland'})
            MERGE (stub:Post:Posting {uuid: 'p-stub'})
            MERGE (stub)-[:MENTIONS]->(a)
            MERGE (good:Post:Posting {uuid: 'p-good'})
              ON CREATE SET good.text = 'post mentioning Deutschland',
                            good.timestamp = datetime('2026-05-04T10:00:00+00:00')
            MERGE (good)-[:MENTIONS]->(a)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="Deutschland", limit=10),
        audit=audit_ctx,
    )

    assert [hit.uuid for hit in out.hits] == ["p-good"]
    assert out.hits[0].matched_name == "Deutschland"


def test_posts_mentioning_time_window_excludes_entity_branch_match(
    migrated_driver: Driver, audit_ctx: AuditContext
) -> None:
    """Entity-branch hits outside the [from, to) window must be excluded.

    Regression test: an earlier revision of the Cypher mixed ``AND``
    and ``OR`` without grouping the OR branches, so the time-window
    predicates only applied to the Alias branch. Posts matched via
    the Entity branch were returned regardless of ``from_``/``to``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
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
        audit=audit_ctx,
    )

    assert out.hits == []


def test_posts_mentioning_finds_resolved_alias_by_canonical_name(
    migrated_driver: Driver, audit_ctx: AuditContext
) -> None:
    """A canonical-name query resolves through Alias -> Entity when present.

    Seeds the graph shape expected after alias resolution lands and
    verifies the tool can still find a post whose ``:MENTIONS`` edge
    terminates at ``:Alias`` rather than directly at ``:Entity``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
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
        audit=audit_ctx,
    )

    assert len(out.hits) == 1
    assert out.hits[0].uuid == "p-resolved"
    assert out.hits[0].entity_id == "ent-berlin"
    assert out.hits[0].matched_name == "Berlin"
    assert out.audit_entities() == ["ent-berlin"]


def test_posts_mentioning_dedups_alias_with_multiple_resolved_edges(
    migrated_driver: Driver, audit_ctx: AuditContext
) -> None:
    """An alias with two :RESOLVED_TO edges must not return its post twice.

    Nothing structurally guarantees one ``:RESOLVED_TO`` edge per alias
    (concurrent resolve / manual fix / threshold change can mint a second).
    The OPTIONAL MATCH onto ``:Entity`` then fans the post out into one row
    per edge, so without per-post aggregation the same post is reported
    multiple times. Regression test for issue #23 (query-side).

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e1:Entity {id: 'ent-a'}) ON CREATE SET e1.canonical_name = 'Berlin'
            MERGE (e2:Entity {id: 'ent-b'}) ON CREATE SET e2.canonical_name = 'Berlin'
            MERGE (a:Alias {surface_form: 'Berlin'})
            MERGE (a)-[:RESOLVED_TO]->(e1)
            MERGE (a)-[:RESOLVED_TO]->(e2)
            MERGE (p:Post:Posting {uuid: 'p-1'})
              ON CREATE SET p.text = 'mentions berlin',
                            p.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(a)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=10),
        audit=audit_ctx,
    )

    assert [hit.uuid for hit in out.hits] == ["p-1"]


def test_posts_mentioning_limit_not_consumed_by_duplicate_edges(
    migrated_driver: Driver, audit_ctx: AuditContext
) -> None:
    """Duplicate rows from a multi-edge alias must not eat ``$limit`` slots.

    With two posts each mentioning an alias that has two ``:RESOLVED_TO``
    edges, a ``limit`` of 2 must still surface both *distinct* posts. Before
    the fix, the newest post's two duplicate rows silently consumed the whole
    limit and the older post vanished. Regression test for issue #23.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e1:Entity {id: 'ent-a'}) ON CREATE SET e1.canonical_name = 'Berlin'
            MERGE (e2:Entity {id: 'ent-b'}) ON CREATE SET e2.canonical_name = 'Berlin'
            MERGE (a:Alias {surface_form: 'Berlin'})
            MERGE (a)-[:RESOLVED_TO]->(e1)
            MERGE (a)-[:RESOLVED_TO]->(e2)
            MERGE (p1:Post:Posting {uuid: 'p-old'})
              ON CREATE SET p1.text = 'older berlin',
                            p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p-new'})
              ON CREATE SET p2.text = 'newer berlin',
                            p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (p1)-[:MENTIONS]->(a)
            MERGE (p2)-[:MENTIONS]->(a)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=2),
        audit=audit_ctx,
    )

    uuids = [hit.uuid for hit in out.hits]
    assert len(uuids) == len(set(uuids))  # no post returned twice
    assert set(uuids) == {"p-old", "p-new"}


def test_posts_mentioning_dedups_post_matched_via_two_aliases(migrated_driver: Driver, audit_ctx: AuditContext) -> None:
    """A post matched via two distinct aliases is returned once, not twice.

    A post can mention the query term through more than one alias (e.g. a
    surface form ``Berlin`` and an abbreviation ``BER`` resolved to the same
    canonical entity). Both ``:MENTIONS`` edges match the query, so per-post
    aggregation is required to avoid a duplicate row.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        audit_ctx: The test's audit context.
    """
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (a1:Alias {surface_form: 'Berlin'})
            MERGE (a2:Alias {surface_form: 'BER'})
            MERGE (a2)-[:RESOLVED_TO]->(e)
            MERGE (p:Post:Posting {uuid: 'p-multi'})
              ON CREATE SET p.text = 'Berlin a.k.a. BER',
                            p.timestamp = datetime('2026-05-05T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(a1)
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )

    out = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=10),
        audit=audit_ctx,
    )

    assert [hit.uuid for hit in out.hits] == ["p-multi"]


def test_audit_event_written(migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """One query event per call, attributed to the caller, with an empty target on no hits."""
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="Nowhere"),
        audit=audit_trail.context(Actor(user="alice", via="edge")),
    )
    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "tool.posts_mentioning", "ok")
    assert event["actor"] == {"user": "alice", "via": "edge"}
    assert event["criteria"] == {"entity": "Nowhere", "from": None, "to": None, "limit": 50}
    assert event["target"] == {"kind": "mixed", "count": 0}


def test_audit_event_references_authors_entities_and_posts(
    migrated_driver: Driver, audit_ctx: AuditContext, audit_stream: io.StringIO
) -> None:
    """The event names whose posts were disclosed (authors and co-authors), the entity, and the posts.

    Post text never reaches the event: only ids and the hit count.
    """
    from chorus.tools.posts_mentioning import PostsMentioningIn, posts_mentioning

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-1'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (alice:Author {id: 'auth-alice'})
            MERGE (bob:Author {id: 'auth-bob'})
            MERGE (p1:Post:Posting {uuid: 'p-1'})
              ON CREATE SET p1.text = 'placeholder text one',
                            p1.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p-2'})
              ON CREATE SET p2.text = 'placeholder text two',
                            p2.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (alice)-[:AUTHORED]->(p1)
            MERGE (bob)-[:CO_AUTHORED]->(p1)
            MERGE (alice)-[:AUTHORED]->(p2)
            MERGE (p1)-[:MENTIONS]->(al)
            MERGE (p2)-[:MENTIONS]->(al)
            """
        )

    out = posts_mentioning(migrated_driver, PostsMentioningIn(entity="berlin"), audit=audit_ctx)
    assert [h.uuid for h in out.hits] == ["p-1", "p-2"]
    assert "author_ids" not in out.model_dump(mode="json")["hits"][0]

    [event] = operation_events(audit_stream)
    assert sorted(event["target"]["ids"][:2]) == ["author:auth-alice", "author:auth-bob"]
    assert event["target"]["ids"][2:] == ["entity:ent-1", "post:p-1", "post:p-2"]
    assert event["target"]["count"] == 2
    assert "placeholder text" not in audit_stream.getvalue()
