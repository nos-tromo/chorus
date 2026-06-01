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


def test_write_mentions_backfills_missing_alias_label(migrated_driver: Driver) -> None:
    """A pre-existing Alias with no label is backfilled on the next mention."""
    from chorus.ingestion.extraction import write_mentions

    with migrated_driver.session() as s:
        s.run(
            "MERGE (p:Post {uuid: 'p1'}) "
            "ON CREATE SET p.text = 'x', p.timestamp = datetime('2026-05-01T00:00:00+00:00')"
        )
        # A stale alias created before write_mentions persisted labels: it
        # exists with no label property at all.
        s.run("MERGE (a:Alias {surface_form: 'Berlin'})")
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


def test_write_mentions_does_not_overwrite_existing_alias_label(migrated_driver: Driver) -> None:
    """A re-mention with a different label never overwrites a label already set."""
    from chorus.ingestion.extraction import write_mentions

    with migrated_driver.session() as s:
        s.run(
            "MERGE (p:Post {uuid: 'p2'}) "
            "ON CREATE SET p.text = 'x', p.timestamp = datetime('2026-05-01T00:00:00+00:00')"
        )

    def span(label: str) -> dict[str, Any]:
        return {
            "surface_form": "Apple",
            "label": label,
            "span_start": 0,
            "span_end": 5,
            "confidence": 0.9,
            "post_uuid": "p2",
            "model_version": "gliner-x",
        }

    assert write_mentions(migrated_driver, "p2", [span("ORG")]) == 1
    assert write_mentions(migrated_driver, "p2", [span("FOOD")]) == 1
    with migrated_driver.session() as s:
        rec = s.run("MATCH (a:Alias {surface_form: 'Apple'}) RETURN a.label AS label").single()
    assert rec is not None
    assert rec["label"] == "ORG"
