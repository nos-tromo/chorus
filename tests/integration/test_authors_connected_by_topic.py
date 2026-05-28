"""authors_connected_by_topic: other authors sharing topics with a seed author."""

from __future__ import annotations

from typing import Any

import pytest
from neo4j import Driver
from pydantic import ValidationError


def test_max_hops_above_one_rejected() -> None:
    """max_hops > 1 is not yet supported and fails input validation (-> 422)."""
    from chorus.tools.authors_connected_by_topic import AuthorsConnectedByTopicIn

    with pytest.raises(ValidationError):
        AuthorsConnectedByTopicIn(seed_author="x", max_hops=2)


def test_empty_seed_returns_one_group_no_connections(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A seed author with no shared topics yields one group with no connections."""
    from chorus.tools.authors_connected_by_topic import (
        AuthorsConnectedByTopicIn,
        authors_connected_by_topic,
    )

    with migrated_driver.session() as s:
        s.run("MERGE (a:Author {id: 'solo'}) ON CREATE SET a.display_name = 'Solo'")
    out = authors_connected_by_topic(
        migrated_driver,
        AuthorsConnectedByTopicIn(seed_author="Solo"),
        user="test-user",
        audit=in_memory_audit,
    )
    assert len(out.results) == 1
    assert out.results[0].seed.author_id == "solo"
    assert out.results[0].connected == []


def test_connected_by_shared_topic_with_overlap(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Only authors clearing min_overlap shared topics are returned, ranked."""
    from chorus.tools.authors_connected_by_topic import (
        AuthorsConnectedByTopicIn,
        authors_connected_by_topic,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (seed:Author {id: 'seed'}) ON CREATE SET seed.display_name = 'Seed'
            MERGE (b:Author {id: 'b'})       ON CREATE SET b.display_name = 'B'
            MERGE (c:Author {id: 'c'})       ON CREATE SET c.display_name = 'C'
            MERGE (berlin:Alias {surface_form: 'Berlin'})
            MERGE (paris:Alias  {surface_form: 'Paris'})
            MERGE (ps:Post:Posting {uuid: 'ps'}) MERGE (seed)-[:AUTHORED]->(ps)
            MERGE (ps)-[:MENTIONS]->(berlin) MERGE (ps)-[:MENTIONS]->(paris)
            MERGE (pb:Post:Posting {uuid: 'pb'}) MERGE (b)-[:AUTHORED]->(pb)
            MERGE (pb)-[:MENTIONS]->(berlin) MERGE (pb)-[:MENTIONS]->(paris)
            MERGE (pc:Post:Posting {uuid: 'pc'}) MERGE (c)-[:AUTHORED]->(pc)
            MERGE (pc)-[:MENTIONS]->(berlin)
            """
        )
    out = authors_connected_by_topic(
        migrated_driver,
        AuthorsConnectedByTopicIn(seed_author="Seed", min_overlap=2),
        user="test-user",
        audit=in_memory_audit,
    )
    conn = out.results[0].connected
    assert [c.author_id for c in conn] == ["b"]  # only B clears min_overlap=2
    assert conn[0].overlap == 2
    assert set(conn[0].shared_topics) == {"Berlin", "Paris"}
    assert out.audit_result_count() == 1


def test_registered_in_tools(migrated_driver: Driver) -> None:
    """The tool self-registers into the global TOOLS registry."""
    from chorus.tools import TOOLS

    assert "authors_connected_by_topic" in TOOLS
