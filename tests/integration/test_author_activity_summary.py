"""author_activity_summary: per-author aggregates over AUTHORED posts."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_empty_returns_no_summaries(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A name matching no author yields an empty summary list, not an error."""
    from chorus.tools.author_activity_summary import (
        AuthorActivitySummaryIn,
        author_activity_summary,
    )

    out = author_activity_summary(
        migrated_driver,
        AuthorActivitySummaryIn(author="nobody"),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.summaries == []
    assert out.audit_result_count() == 0


def test_aggregates_counts_and_topics(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A seeded author's posts aggregate by type with engagement + top topics."""
    from chorus.tools.author_activity_summary import (
        AuthorActivitySummaryIn,
        author_activity_summary,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a:Author {id: 'auth-1'})
              ON CREATE SET a.handle = 'jane', a.display_name = 'Jane Doe', a.platform = 'X'
            MERGE (p1:Post:Posting {uuid: 'po-1'})
              ON CREATE SET p1.timestamp = datetime('2026-05-01T10:00:00+00:00'),
                            p1.expected_reactions = 10, p1.collected_reactions = 7
            MERGE (p2:Post:Comment {uuid: 'co-1'})
              ON CREATE SET p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p1)
            MERGE (a)-[:AUTHORED]->(p2)
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (p1)-[:MENTIONS]->(al)
            """
        )

    out = author_activity_summary(
        migrated_driver,
        AuthorActivitySummaryIn(author="Jane Doe"),
        user="test-user",
        audit=in_memory_audit,
    )
    assert len(out.summaries) == 1
    su = out.summaries[0]
    assert su.author_id == "auth-1"
    assert su.post_count == 2
    assert su.posting_count == 1
    assert su.comment_count == 1
    assert su.message_count == 0
    assert su.collected_reactions_total == 7
    assert su.expected_reactions_total == 10
    assert su.top_topics[0].topic == "Berlin"
    assert su.top_topics[0].entity_id is None  # unresolved alias today
    assert out.audit_result_count() == 1


def test_time_window_excludes_out_of_range_posts(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Regression: a [from, to) bound must constrain the aggregation."""
    from chorus.tools.author_activity_summary import (
        AuthorActivitySummaryIn,
        author_activity_summary,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a:Author {id: 'auth-2'})
              ON CREATE SET a.handle = 'bob', a.display_name = 'Bob'
            MERGE (p:Post:Posting {uuid: 'old-1'})
              ON CREATE SET p.timestamp = datetime('2026-01-01T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            """
        )
    out = author_activity_summary(
        migrated_driver,
        AuthorActivitySummaryIn.model_validate(
            {
                "author": "bob",
                "from": "2026-05-01T00:00:00+00:00",
                "to": "2026-06-01T00:00:00+00:00",
            }
        ),
        user="test-user",
        audit=in_memory_audit,
    )
    assert len(out.summaries) == 1  # author still matched
    assert out.summaries[0].post_count == 0  # but the out-of-range post is excluded


def test_same_name_authors_not_merged(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Two distinct authors sharing a display name return two summaries."""
    from chorus.tools.author_activity_summary import (
        AuthorActivitySummaryIn,
        author_activity_summary,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a1:Author {id: 'dup-1'}) ON CREATE SET a1.display_name = 'Sam'
            MERGE (a2:Author {id: 'dup-2'}) ON CREATE SET a2.display_name = 'Sam'
            """
        )
    out = author_activity_summary(
        migrated_driver,
        AuthorActivitySummaryIn(author="Sam"),
        user="test-user",
        audit=in_memory_audit,
    )
    assert {s.author_id for s in out.summaries} == {"dup-1", "dup-2"}


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "author_activity_summary" in TOOLS
