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
                "CALL db.index.vector.queryNodes('entity_embedding', 10, $v) YIELD node RETURN node.id AS id",
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

    cands = cluster_candidates(migrated_driver, _vec(0.99, 0.02), threshold=0.86, k=5, entity_type="LOCATION")
    ids = [c["id"] for c in cands]
    assert "e-berlin" in ids  # close + same type
    assert "e-merkel" not in ids  # close but wrong type
    assert "e-paris" not in ids  # same type but orthogonal (below threshold)
    assert cands[0]["canonical_name"] == "Berlin"
    assert cands[0]["type"] == "LOCATION"


def test_mint_entity_creates_typed_entity(migrated_driver: Driver) -> None:
    """mint_entity creates an :Entity with name, type, embedding, null description."""
    from chorus.ingestion.resolution import mint_entity

    eid = mint_entity(migrated_driver, "Bratwurst", _vec(0.5, 0.5), entity_type="FOOD")
    assert eid
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (e:Entity {id: $id}) RETURN e.canonical_name AS n, e.type AS t, e.description AS d",
            id=eid,
        ).single()
    assert rec is not None
    assert rec["n"] == "Bratwurst"
    assert rec["t"] == "FOOD"
    assert rec["d"] is None


def test_llm_tiebreaker_picks_and_abstains(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tie-breaker returns a chosen id, or None on abstain/ambiguous output."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import llm_tiebreaker

    candidates = [
        {"id": "e-1", "canonical_name": "Joe Biden", "type": "PERSON", "score": 0.9},
        {"id": "e-2", "canonical_name": "Jill Biden", "type": "PERSON", "score": 0.88},
    ]
    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "e-1")
    assert llm_tiebreaker("President Biden", candidates) == "e-1"

    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "NONE")
    assert llm_tiebreaker("President Biden", candidates) is None

    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "e-1 or maybe e-2")
    assert llm_tiebreaker("President Biden", candidates) is None


def test_resolve_alias_mints_when_no_candidates(migrated_driver: Driver) -> None:
    """With an empty entity set, an alias mints a typed entity (method=minted)."""
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Solingen'})")
    eid, method = resolve_alias_to_entity(
        migrated_driver,
        "Solingen",
        _vec(0.3, 0.7),
        load_resolution_env(),
        entity_type="LOCATION",
        embed_model="bge-m3",
    )
    assert method == "minted"
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (a:Alias {surface_form: 'Solingen'})-[r:RESOLVED_TO]->(e:Entity {id: $id}) "
            "RETURN r.method AS m, e.type AS t",
            id=eid,
        ).single()
    assert rec is not None
    assert rec["m"] == "minted"
    assert rec["t"] == "LOCATION"


def test_resolve_alias_attaches_to_single_candidate(migrated_driver: Driver) -> None:
    """An alias close to one same-type entity attaches (method=vector_single)."""
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    _seed_entity(migrated_driver, "e-berlin", "Berlin", "LOCATION", _vec(1.0))
    _await_vector(migrated_driver, "e-berlin", _vec(0.99, 0.01))
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Berlin '})")

    eid, method = resolve_alias_to_entity(
        migrated_driver,
        "Berlin ",
        _vec(0.99, 0.01),
        load_resolution_env(),
        entity_type="LOCATION",
        embed_model="bge-m3",
    )
    assert eid == "e-berlin"
    assert method == "vector_single"


def test_resolve_alias_is_idempotent(migrated_driver: Driver) -> None:
    """Re-resolving an already-resolved alias is a no-op returning method=skipped."""
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Aachen'})")
    cfg = load_resolution_env()
    eid1, _ = resolve_alias_to_entity(
        migrated_driver, "Aachen", _vec(0.2, 0.9), cfg, entity_type="LOCATION", embed_model="bge-m3"
    )
    eid2, method2 = resolve_alias_to_entity(
        migrated_driver, "Aachen", _vec(0.2, 0.9), cfg, entity_type="LOCATION", embed_model="bge-m3"
    )
    assert eid2 == eid1
    assert method2 == "skipped"


def test_resolve_all_clusters_and_is_rerunnable(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_all clusters case-variant aliases and is a no-op on re-run."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (p:Post {uuid: 'pp'}) ON CREATE SET p.text='t',
                  p.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (a1:Alias {surface_form: 'Berlin'})  ON CREATE SET a1.label='LOCATION'
            MERGE (a2:Alias {surface_form: 'berlin'})  ON CREATE SET a2.label='LOCATION'
            MERGE (a3:Alias {surface_form: 'Merkel'})  ON CREATE SET a3.label='PERSON'
            MERGE (p)-[:MENTIONS]->(a1)
            MERGE (p)-[:MENTIONS]->(a2)
            MERGE (p)-[:MENTIONS]->(a3)
            """
        )

    vectors = {"Berlin": _vec(1.0), "berlin": _vec(1.0), "Merkel": _vec(0.0, 1.0)}
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [vectors[t] for t in texts])

    summary = resolve_all(migrated_driver, load_resolution_env())
    assert summary.processed == 3
    assert summary.minted == 2  # one LOCATION entity + one PERSON entity

    with migrated_driver.session() as s:
        n_rec = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()
        same_rec = s.run(
            "MATCH (:Alias {surface_form:'Berlin'})-[:RESOLVED_TO]->(e1), "
            "(:Alias {surface_form:'berlin'})-[:RESOLVED_TO]->(e2) "
            "RETURN e1.id = e2.id AS same"
        ).single()
    assert n_rec is not None
    assert same_rec is not None
    assert n_rec["n"] == 2
    assert same_rec["same"] is True

    again = resolve_all(migrated_driver, load_resolution_env())
    assert again.processed == 0
