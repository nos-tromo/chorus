"""extraction.write_mentions persists the GLiNER label on the Alias."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_write_mentions_stores_alias_label(migrated_driver: Driver) -> None:
    """The GLiNER label is stored on the Alias so resolution can set Entity.type."""
    from chorus.ingestion.extraction import write_mentions

    with migrated_driver.session() as s:
        s.run(
            "MERGE (p:Post {uuid: 'p1'}) "
            "ON CREATE SET p.text = 'x', p.timestamp = datetime('2026-05-01T00:00:00+00:00')"
        )
    spans: list[dict[str, Any]] = [
        {
            "surface_form": "Berlin",
            "label": "LOCATION",
            "span_start": 0,
            "span_end": 6,
            "confidence": 0.9,
            "post_uuid": "p1",
            "model_version": "gliner-x",
        }
    ]
    assert write_mentions(migrated_driver, "p1", spans) == 1
    with migrated_driver.session() as s:
        rec = s.run("MATCH (a:Alias {surface_form: 'Berlin'}) RETURN a.label AS label").single()
    assert rec is not None
    assert rec["label"] == "LOCATION"
