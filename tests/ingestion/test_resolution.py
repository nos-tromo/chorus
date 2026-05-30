"""Entity-resolution pipeline tests (vector search, mint, tie-break, batch)."""

from __future__ import annotations

import time

import pytest
from neo4j import Driver

EMBED_DIM = 1024


def _vec(*head: float) -> list[float]:
    """A 1024-d vector with the given leading components, zero-padded."""
    v = [0.0] * EMBED_DIM
    for i, x in enumerate(head):
        v[i] = float(x)
    return v


def _await_vector(driver: Driver, expected_id: str, query: list[float], tries: int = 50) -> None:
    """Poll the vector index until ``expected_id`` is searchable (index lag)."""
    for _ in range(tries):
        with driver.session() as s:
            rows = s.run(
                "CALL db.index.vector.queryNodes('entity_embedding', 10, $v) "
                "YIELD node RETURN node.id AS id",
                v=query,
            ).data()
        if any(r["id"] == expected_id for r in rows):
            return
        time.sleep(0.1)
    raise AssertionError(f"{expected_id} not searchable in time")


def _seed_entity(driver: Driver, eid: str, name: str, etype: str, vec: list[float]) -> None:
    with driver.session() as s:
        s.run(
            "CREATE (:Entity {id: $id, canonical_name: $name, type: $type, embedding: $vec})",
            id=eid,
            name=name,
            type=etype,
            vec=vec,
        )


def test_cluster_candidates_threshold_and_type_filter(migrated_driver: Driver) -> None:
    """Candidates are filtered by cosine threshold and (when given) entity type."""
    from chorus.ingestion.resolution import cluster_candidates

    _seed_entity(migrated_driver, "e-berlin", "Berlin", "LOCATION", _vec(1.0))
    _seed_entity(migrated_driver, "e-paris", "Paris", "LOCATION", _vec(0.0, 1.0))
    _seed_entity(migrated_driver, "e-merkel", "Merkel", "PERSON", _vec(0.99, 0.01))
    _await_vector(migrated_driver, "e-berlin", _vec(0.99, 0.02))

    cands = cluster_candidates(
        migrated_driver, _vec(0.99, 0.02), threshold=0.86, k=5, entity_type="LOCATION"
    )
    ids = [c["id"] for c in cands]
    assert "e-berlin" in ids  # close + same type
    assert "e-merkel" not in ids  # close but wrong type
    assert "e-paris" not in ids  # same type but orthogonal (below threshold)
    assert cands[0]["canonical_name"] == "Berlin"
    assert cands[0]["type"] == "LOCATION"
