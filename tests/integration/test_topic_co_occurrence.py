"""topic_co_occurrence: topics co-mentioned with a seed topic in the same posts."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_empty_returns_nothing(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An empty graph yields no co-occurring topics and echoes the seed."""
    from chorus.tools.topic_co_occurrence import (
        TopicCoOccurrenceIn,
        topic_co_occurrence,
    )

    out = topic_co_occurrence(
        migrated_driver,
        TopicCoOccurrenceIn(topic="Berlin"),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.cooccurring == []
    assert out.seed == "Berlin"


def test_cooccurrence_ranked_and_excludes_seed(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Co-occurring topics rank by shared-post count; the seed is excluded."""
    from chorus.tools.topic_co_occurrence import (
        TopicCoOccurrenceIn,
        topic_co_occurrence,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Alias {surface_form: 'Berlin'})
            MERGE (paris:Alias {surface_form: 'Paris'})
            MERGE (rome:Alias  {surface_form: 'Rome'})
            MERGE (p1:Post:Posting {uuid: 't-1'})
              ON CREATE SET p1.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 't-2'})
              ON CREATE SET p2.timestamp = datetime('2026-05-02T00:00:00+00:00')
            MERGE (p1)-[:MENTIONS]->(seed)  MERGE (p1)-[:MENTIONS]->(paris)
            MERGE (p2)-[:MENTIONS]->(seed)  MERGE (p2)-[:MENTIONS]->(paris)
            MERGE (p2)-[:MENTIONS]->(rome)
            """
        )
    out = topic_co_occurrence(
        migrated_driver,
        TopicCoOccurrenceIn(topic="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    names = [c.topic for c in out.cooccurring]
    assert "Berlin" not in names  # seed excluded
    assert names[0] == "Paris"  # 2 shared posts ranks above Rome (1)
    assert out.cooccurring[0].count == 2
    assert {c.topic for c in out.cooccurring} == {"Paris", "Rome"}


def test_time_window_excludes_out_of_range(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Regression: [from, to) must constrain which posts count toward co-occurrence."""
    from chorus.tools.topic_co_occurrence import (
        TopicCoOccurrenceIn,
        topic_co_occurrence,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Alias {surface_form: 'Berlin'})
            MERGE (paris:Alias {surface_form: 'Paris'})
            MERGE (p:Post:Posting {uuid: 'old-co'})
              ON CREATE SET p.timestamp = datetime('2026-01-01T00:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(seed) MERGE (p)-[:MENTIONS]->(paris)
            """
        )
    out = topic_co_occurrence(
        migrated_driver,
        TopicCoOccurrenceIn.model_validate(
            {
                "topic": "berlin",
                "from": "2026-05-01T00:00:00+00:00",
                "to": "2026-06-01T00:00:00+00:00",
            }
        ),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.cooccurring == []


def test_seed_by_resolved_alias_surface_form(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Seeding by an alias surface form still works after that alias resolved.

    When ``Berlín`` resolves to an entity whose canonical name is ``Berlin``,
    seeding the tool with the surface form ``Berlín`` must still find the
    co-occurring topics, and the seed's own entity must be excluded from its
    list (identity-based exclusion, not a display-name string compare).
    """
    from chorus.tools.topic_co_occurrence import (
        TopicCoOccurrenceIn,
        topic_co_occurrence,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Entity {id: 'e-berlin'})
              ON CREATE SET berlin.canonical_name = 'Berlin', berlin.type = 'LOC'
            MERGE (variant:Alias {surface_form: 'Berlín'})
            MERGE (variant)-[:RESOLVED_TO]->(berlin)
            MERGE (paris:Alias {surface_form: 'Paris'})
            MERGE (p:Post:Posting {uuid: 'r-1'})
              ON CREATE SET p.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(variant)
            MERGE (p)-[:MENTIONS]->(paris)
            """
        )
    out = topic_co_occurrence(
        migrated_driver,
        TopicCoOccurrenceIn(topic="Berlín", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    names = [c.topic for c in out.cooccurring]
    assert "Berlin" not in names  # seed entity excluded even when seeded by its alias
    assert names == ["Paris"]
    assert out.cooccurring[0].count == 1


def test_seed_resolves_across_sibling_surface_forms(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Seeding by one surface form spans posts that mention the entity via another.

    ``Berlín`` and ``Berlin`` are two surface forms of the same resolved entity.
    Seeding ``Berlín`` must find co-occurrences in the post that used the sibling
    surface form ``Berlin`` too — the seed is resolved to its entity identity, not
    matched as a literal string.
    """
    from chorus.tools.topic_co_occurrence import (
        TopicCoOccurrenceIn,
        topic_co_occurrence,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (berlin:Entity {id: 'e-berlin'})
              ON CREATE SET berlin.canonical_name = 'Berlin', berlin.type = 'LOC'
            MERGE (a1:Alias {surface_form: 'Berlín'})
            MERGE (a2:Alias {surface_form: 'Berlin'})
            MERGE (a1)-[:RESOLVED_TO]->(berlin)
            MERGE (a2)-[:RESOLVED_TO]->(berlin)
            MERGE (paris:Alias {surface_form: 'Paris'})
            MERGE (rome:Alias  {surface_form: 'Rome'})
            MERGE (p1:Post:Posting {uuid: 's-1'})
              ON CREATE SET p1.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 's-2'})
              ON CREATE SET p2.timestamp = datetime('2026-05-02T00:00:00+00:00')
            MERGE (p1)-[:MENTIONS]->(a1)    MERGE (p1)-[:MENTIONS]->(paris)
            MERGE (p2)-[:MENTIONS]->(a2)    MERGE (p2)-[:MENTIONS]->(rome)
            """
        )
    out = topic_co_occurrence(
        migrated_driver,
        TopicCoOccurrenceIn(topic="Berlín", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    names = {c.topic for c in out.cooccurring}
    assert "Berlin" not in names  # seed entity excluded under both surface forms
    assert names == {"Paris", "Rome"}  # Rome reached via the sibling 'Berlin' mention


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "topic_co_occurrence" in TOOLS
