"""After resolve_all, the graph tools cluster aliases by their resolved entity."""

from __future__ import annotations

from typing import Any

import pytest
from neo4j import Driver


def _vec(*head: float) -> list[float]:
    """A 1024-d vector with the given leading components, zero-padded."""
    v = [0.0] * 1024
    for i, x in enumerate(head):
        v[i] = float(x)
    return v


def test_resolution_lets_topic_tool_cluster(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """topic_co_occurrence sees one Berlin *entity* (count 2), not two aliases."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.tools.topic_co_occurrence import TopicCoOccurrenceIn, topic_co_occurrence
    from chorus.utils.env_cfg import load_resolution_env

    # Post r1 mentions "Berlin" + "Spree"; r2 mentions "berlin" + "Spree".
    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (p1:Post:Posting {uuid:'r1'}) ON CREATE SET p1.timestamp=datetime('2026-05-01T00:00:00+00:00')
            MERGE (p2:Post:Posting {uuid:'r2'}) ON CREATE SET p2.timestamp=datetime('2026-05-02T00:00:00+00:00')
            MERGE (b1:Alias {surface_form:'Berlin'}) ON CREATE SET b1.label='LOCATION'
            MERGE (b2:Alias {surface_form:'berlin'}) ON CREATE SET b2.label='LOCATION'
            MERGE (sp:Alias {surface_form:'Spree'})  ON CREATE SET sp.label='LOCATION'
            MERGE (p1)-[:MENTIONS]->(b1) MERGE (p1)-[:MENTIONS]->(sp)
            MERGE (p2)-[:MENTIONS]->(b2) MERGE (p2)-[:MENTIONS]->(sp)
            """
        )
    vectors = {"Berlin": _vec(1.0), "berlin": _vec(1.0), "Spree": _vec(0.0, 1.0)}
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [vectors[t] for t in texts])

    resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="test")

    out = topic_co_occurrence(
        migrated_driver,
        TopicCoOccurrenceIn(topic="Spree", limit=10),
        user="t",
        audit=in_memory_audit,
    )
    berlin = [c for c in out.cooccurring if c.entity_id is not None and c.topic == "Berlin"]
    assert len(berlin) == 1  # one resolved entity, not two alias surface forms
    assert berlin[0].count == 2  # both posts, via the one resolved entity
