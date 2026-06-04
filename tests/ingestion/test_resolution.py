"""Entity-resolution pipeline tests (vector search, mint, tie-break, batch)."""

from __future__ import annotations

import time
from typing import Any

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


def test_mint_entity_creates_typed_entity_and_links_alias(migrated_driver: Driver) -> None:
    """mint_entity atomically creates the typed :Entity and its RESOLVED_TO edge."""
    from chorus.ingestion.resolution import mint_entity

    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Bratwurst'})")
    eid = mint_entity(
        migrated_driver, "Bratwurst", _vec(0.5, 0.5), entity_type="FOOD", embed_model="bge-m3", norm_key="bratwurst"
    )
    assert eid
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (a:Alias {surface_form: 'Bratwurst'})-[r:RESOLVED_TO]->(e:Entity {id: $id}) "
            "RETURN e.canonical_name AS n, e.type AS t, e.description AS d, r.method AS m, a.norm_key AS nk",
            id=eid,
        ).single()
    assert rec is not None
    assert rec["n"] == "Bratwurst"
    assert rec["t"] == "FOOD"
    assert rec["d"] is None
    assert rec["m"] == "minted"
    assert rec["nk"] == "bratwurst"


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


def test_llm_tiebreaker_exact_id_match_not_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matching is by exact id token, so 'e-1' is not matched by a reply of 'e-12'."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import llm_tiebreaker

    candidates = [
        {"id": "e-1", "canonical_name": "Apple Inc", "type": "ORG", "score": 0.9},
        {"id": "e-12", "canonical_name": "Apple Records", "type": "ORG", "score": 0.88},
    ]
    # Reply names e-12 exactly; substring logic would also match e-1 -> wrongly abstain.
    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "e-12")
    assert llm_tiebreaker("Apple", candidates) == "e-12"

    # And a bare 'e-1' must select e-1, not also trip on e-12.
    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "e-1")
    assert llm_tiebreaker("Apple", candidates) == "e-1"


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


def test_mint_path_is_atomic_no_orphan_without_alias(migrated_driver: Driver) -> None:
    """Minting for a surface whose :Alias node is absent creates no orphan Entity."""
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    # No :Alias node is created for 'Ghost'. The mint path must not leave a
    # dangling :Entity with no incoming :RESOLVED_TO.
    with pytest.raises(Exception):  # noqa: B017 — any failure is acceptable; the invariant is "no orphan"
        resolve_alias_to_entity(
            migrated_driver,
            "Ghost",
            _vec(0.4, 0.6),
            load_resolution_env(),
            entity_type="LOCATION",
            embed_model="bge-m3",
        )
    with migrated_driver.session() as s:
        rec = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()
    assert rec is not None
    assert rec["n"] == 0  # no orphan entity was created


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
    """Re-resolving an already-resolved alias returns the same entity (method=cross_run).

    The durable norm_key lookup finds the alias's own prior resolution, so the
    second call re-attaches to the identical entity idempotently rather than
    minting again. The cardinality guard keeps it at a single edge.
    """
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    # The alias label matches the entity_type passed below — as it always does
    # in resolve_all, which reads entity_type from a.label. The durable lookup
    # keys on (norm_key, label), so this consistency is what makes the re-run
    # self-match its prior resolution.
    with migrated_driver.session() as s:
        s.run("MERGE (a:Alias {surface_form: 'Aachen'}) ON CREATE SET a.label='LOCATION'")
    cfg = load_resolution_env()
    eid1, _ = resolve_alias_to_entity(
        migrated_driver, "Aachen", _vec(0.2, 0.9), cfg, entity_type="LOCATION", embed_model="bge-m3"
    )
    eid2, method2 = resolve_alias_to_entity(
        migrated_driver, "Aachen", _vec(0.2, 0.9), cfg, entity_type="LOCATION", embed_model="bge-m3"
    )
    assert eid2 == eid1
    assert method2 == "cross_run"
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (a:Alias {surface_form:'Aachen'}) OPTIONAL MATCH (a)-[r:RESOLVED_TO]->() RETURN count(r) AS edges"
        ).single()
    assert rec is not None and rec["edges"] == 1  # idempotent: still exactly one edge


def test_resolve_all_clusters_and_is_rerunnable(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # "berlin" is given a vector ORTHOGONAL to "Berlin": the two would never
    # vector-match, so the ONLY thing that can cluster them is the normalized
    # (surface, label) cache. If the cache were removed, "berlin" would mint a
    # third entity and these assertions would fail — that is what makes this
    # test actually exercise the cache path.
    vectors = {"Berlin": _vec(1.0), "berlin": _vec(0.0, 0.0, 1.0), "Merkel": _vec(0.0, 1.0)}
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [vectors[t] for t in texts])

    summary = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="test")
    assert summary.processed == 3
    assert summary.minted == 2  # one LOCATION entity + one PERSON entity
    assert summary.attached_cache == 1  # "berlin" clustered via the cache, not vectors

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

    # Both case-variant aliases now carry the durable normalized key.
    with migrated_driver.session() as s:
        keys = sorted(
            r["k"] for r in s.run("MATCH (a:Alias) WHERE a.surface_form IN ['Berlin','berlin'] RETURN a.norm_key AS k")
        )
    assert keys == ["berlin", "berlin"]

    again = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="test")
    assert again.processed == 0


def test_resolve_alias_mints_when_llm_rejects_all_candidates(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tie-break enabled + LLM affirms no candidate → mint, not attach to top-score."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    # Two near-parallel PERSON entities both clear the threshold for the query.
    _seed_entity(migrated_driver, "e-a", "Joe Biden", "PERSON", _vec(1.0))
    _seed_entity(migrated_driver, "e-b", "Jill Biden", "PERSON", _vec(1.0, 0.05))
    _await_vector(migrated_driver, "e-a", _vec(1.0, 0.02))
    _await_vector(migrated_driver, "e-b", _vec(1.0, 0.02))
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'President Biden'})")

    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "NONE")
    eid, method = resolve_alias_to_entity(
        migrated_driver,
        "President Biden",
        _vec(1.0, 0.02),
        load_resolution_env(),  # llm_tiebreak_enabled defaults True
        entity_type="PERSON",
        embed_model="bge-m3",
    )
    assert method == "minted"
    assert eid not in {"e-a", "e-b"}
    with migrated_driver.session() as s:
        rec = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()
    assert rec is not None
    assert rec["n"] == 3  # the new entity was minted, not merged


def test_resolve_alias_topk_when_tiebreak_disabled(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tie-break disabled (never consulted) → attach to top-score, never mint."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import ResolutionConfig

    _seed_entity(migrated_driver, "e-a", "Joe Biden", "PERSON", _vec(1.0))
    _seed_entity(migrated_driver, "e-b", "Jill Biden", "PERSON", _vec(1.0, 0.05))
    _await_vector(migrated_driver, "e-a", _vec(1.0, 0.02))
    _await_vector(migrated_driver, "e-b", _vec(1.0, 0.02))
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'President Biden'})")

    # If the LLM were (wrongly) consulted, this would raise and fail the test.
    def _boom(messages: list[dict[str, str]], **kw: object) -> str:
        raise AssertionError("LLM must not be consulted when tie-break is disabled")

    monkeypatch.setattr(provider, "chat", _boom)
    cfg = ResolutionConfig(
        embed_cluster_threshold=0.86,
        llm_tiebreak_enabled=False,
        case_normalize=True,
        vector_k=5,
    )
    eid, method = resolve_alias_to_entity(
        migrated_driver,
        "President Biden",
        _vec(1.0, 0.02),
        cfg,
        entity_type="PERSON",
        embed_model="bge-m3",
    )
    assert method == "vector_topk"
    assert eid in {"e-a", "e-b"}
    with migrated_driver.session() as s:
        rec = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()
    assert rec is not None
    assert rec["n"] == 2  # nothing minted


def test_resolve_all_does_not_merge_different_label_variants(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same normalized surface but different labels must NOT collapse via the cache."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (p:Post {uuid: 'pp'}) ON CREATE SET p.text='t',
                  p.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (a1:Alias {surface_form: 'Apple'}) ON CREATE SET a1.label='ORG'
            MERGE (a2:Alias {surface_form: 'apple'}) ON CREATE SET a2.label='FOOD'
            MERGE (p)-[:MENTIONS]->(a1)
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )
    # Orthogonal vectors so they would not vector-match even if types allowed it.
    vectors = {"Apple": _vec(1.0), "apple": _vec(0.0, 1.0)}
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [vectors[t] for t in texts])

    summary = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="test")
    assert summary.processed == 2
    assert summary.minted == 2  # two distinct entities, not one

    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (:Alias {surface_form:'Apple'})-[:RESOLVED_TO]->(e1), "
            "(:Alias {surface_form:'apple'})-[:RESOLVED_TO]->(e2) "
            "RETURN e1.id <> e2.id AS distinct, e1.type AS t1, e2.type AS t2"
        ).single()
    assert rec is not None
    assert rec["distinct"] is True
    assert {rec["t1"], rec["t2"]} == {"ORG", "FOOD"}


def test_cluster_candidates_untyped_query_only_matches_untyped(migrated_driver: Driver) -> None:
    """A None entity_type matches only untyped entities, never a typed one."""
    from chorus.ingestion.resolution import cluster_candidates

    _seed_entity(migrated_driver, "e-typed", "Berlin", "LOCATION", _vec(1.0))
    # an untyped entity (type is null), close to the same query
    with migrated_driver.session() as s:
        s.run(
            "CREATE (:Entity {id: 'e-untyped', canonical_name: 'Berlin', type: null, embedding: $v})",
            v=_vec(1.0, 0.01),
        )
    _await_vector(migrated_driver, "e-typed", _vec(1.0, 0.02))
    _await_vector(migrated_driver, "e-untyped", _vec(1.0, 0.02))

    cands = cluster_candidates(migrated_driver, _vec(1.0, 0.02), threshold=0.86, k=5, entity_type=None)
    ids = [c["id"] for c in cands]
    assert "e-untyped" in ids  # untyped query matches the untyped entity
    assert "e-typed" not in ids  # but NOT a typed entity (no cross-type leak)


def test_resolve_all_aborts_cleanly_on_embed_count_mismatch(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If provider.embed returns the wrong number of vectors, fail before any write."""
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (a1:Alias {surface_form: 'Berlin'}) ON CREATE SET a1.label='LOCATION'
            MERGE (a2:Alias {surface_form: 'Paris'})  ON CREATE SET a2.label='LOCATION'
            """
        )
    # Return one fewer (valid, non-zero) vector than inputs — a misbehaving backend.
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_vec(1.0) for _ in texts[:-1]])

    with pytest.raises(ValueError, match="embed"):
        resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="test")

    # Nothing was written: no entities, no RESOLVED_TO edges.
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (e:Entity) WITH count(e) AS ents MATCH (:Alias)-[r:RESOLVED_TO]->() RETURN ents, count(r) AS rels"
        ).single()
    n_ents = 0 if rec is None else rec["ents"]
    assert n_ents == 0


def test_resolve_all_writes_one_audit_row(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolve run writes exactly one §76 audit row with entities + counts."""
    import json
    import sqlite3

    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (p:Post {uuid: 'pp'}) ON CREATE SET p.text='t',
                  p.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (a1:Alias {surface_form: 'Berlin'}) ON CREATE SET a1.label='LOCATION'
            MERGE (a2:Alias {surface_form: 'Spree'})  ON CREATE SET a2.label='LOCATION'
            MERGE (p)-[:MENTIONS]->(a1)
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )
    vectors = {"Berlin": _vec(1.0), "Spree": _vec(0.0, 1.0)}
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [vectors[t] for t in texts])

    summary = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="alice")
    assert summary.processed == 2

    rows = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT user, tool_name, result_count, status, entities_touched_json FROM audit_log")
        .fetchall()
    )
    assert len(rows) == 1
    user, tool_name, result_count, status, entities_json = rows[0]
    assert user == "alice"
    assert tool_name == "resolve_all"
    assert result_count == 2
    assert status == "ok"

    touched = json.loads(entities_json)
    with migrated_driver.session() as s:
        ids = [r["id"] for r in s.run("MATCH (e:Entity) RETURN e.id AS id")]
    assert sorted(touched) == sorted(ids)
    assert len(touched) == 2


def test_resolve_all_audits_failure(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed run still writes an audit row with status='error'."""
    import sqlite3

    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            "MERGE (a:Alias {surface_form: 'Berlin'}) ON CREATE SET a.label='LOCATION' "
            "MERGE (b:Alias {surface_form: 'Paris'}) ON CREATE SET b.label='LOCATION'"
        )
    # return one fewer vector than inputs -> the length-check ValueError (finding #6)
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_vec(1.0) for _ in texts[:-1]])

    with pytest.raises(ValueError, match="embed"):
        resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="bob")

    rows = sqlite3.connect(in_memory_audit.db_path).execute("SELECT status, error_message FROM audit_log").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "error"
    assert rows[0][1] is not None


def test_resolve_all_empty_writes_no_audit_row(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An empty run (no unresolved aliases) writes no audit row (early return)."""
    import sqlite3

    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    summary = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="cli")
    assert summary.processed == 0
    row = sqlite3.connect(in_memory_audit.db_path).execute("SELECT count(*) FROM audit_log").fetchone()
    assert row[0] == 0


def test_cross_run_dedup_below_threshold(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case-variant seen in a LATER run resolves to the earlier entity (#24).

    The in-run cache only collapses variants within a single run. Here
    ``resolve_all`` runs twice: ``Berlin`` first (mints e1, stamps
    ``norm_key='berlin'``), then ``berlin`` in a fresh run with a vector
    ORTHOGONAL to ``Berlin`` so nothing but the durable ``norm_key`` can link
    them. Without the durable key, run 2 mints a duplicate entity.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
        monkeypatch: pytest monkeypatch fixture.
    """
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (p:Post {uuid: 'pp'}) ON CREATE SET p.text='t',
                  p.timestamp = datetime('2026-05-01T00:00:00+00:00')
            MERGE (a1:Alias {surface_form: 'Berlin'}) ON CREATE SET a1.label='LOCATION'
            MERGE (p)-[:MENTIONS]->(a1)
            """
        )
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_vec(1.0) for _ in texts])
    run1 = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="t")
    assert run1.minted == 1

    with migrated_driver.session() as s:
        nk = s.run("MATCH (a:Alias {surface_form:'Berlin'}) RETURN a.norm_key AS k").single()
    assert nk is not None and nk["k"] == "berlin"

    # Second run: a NEW 'berlin' alias with a vector orthogonal to 'Berlin'.
    with migrated_driver.session() as s:
        s.run(
            """
            MATCH (p:Post {uuid:'pp'})
            MERGE (a2:Alias {surface_form: 'berlin'}) ON CREATE SET a2.label='LOCATION'
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_vec(0.0, 0.0, 1.0) for _ in texts])
    run2 = resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="t")
    assert run2.processed == 1

    with migrated_driver.session() as s:
        n = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()
        same = s.run(
            "MATCH (:Alias {surface_form:'Berlin'})-[:RESOLVED_TO]->(e1), "
            "(:Alias {surface_form:'berlin'})-[:RESOLVED_TO]->(e2) RETURN e1.id=e2.id AS same"
        ).single()
    assert n is not None and n["n"] == 1  # no duplicate entity minted across runs
    assert same is not None and same["same"] is True
    assert run2.minted == 0
    assert run2.attached_cross_run == 1


def test_cross_run_respects_label(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable cross-run dedup must not collapse same-surface, different-label aliases.

    ``Apple`` (ORG) in run 1 and ``apple`` (FOOD) in run 2 share a normalized
    surface but differ in type, so the norm_key lookup's label predicate must
    keep them as two distinct entities.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        in_memory_audit: Fresh audit logger over a temp SQLite file.
        monkeypatch: pytest monkeypatch fixture.
    """
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (p:Post {uuid:'pp'}) ON CREATE SET p.text='t',
                  p.timestamp=datetime('2026-05-01T00:00:00+00:00')
            MERGE (a1:Alias {surface_form:'Apple'}) ON CREATE SET a1.label='ORG'
            MERGE (p)-[:MENTIONS]->(a1)
            """
        )
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_vec(1.0) for _ in texts])
    resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="t")

    with migrated_driver.session() as s:
        s.run(
            """
            MATCH (p:Post {uuid:'pp'})
            MERGE (a2:Alias {surface_form:'apple'}) ON CREATE SET a2.label='FOOD'
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_vec(0.0, 1.0) for _ in texts])
    resolve_all(migrated_driver, load_resolution_env(), in_memory_audit, user="t")

    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (:Alias {surface_form:'Apple'})-[:RESOLVED_TO]->(e1), "
            "(:Alias {surface_form:'apple'})-[:RESOLVED_TO]->(e2) RETURN e1.id<>e2.id AS distinct"
        ).single()
        n = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()
    assert rec is not None and rec["distinct"] is True
    assert n is not None and n["n"] == 2


def test_norm_key_set_on_attach_and_mint(migrated_driver: Driver) -> None:
    """Both the mint path and the attach path stamp norm_key on the alias.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    cfg = load_resolution_env()

    # Mint path: no candidates -> mint; norm_key stamped on the alias.
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Mainz'})")
    resolve_alias_to_entity(migrated_driver, "Mainz", _vec(0.2, 0.8), cfg, entity_type="LOCATION", embed_model="m")

    # Attach path: a close same-type entity -> attach; norm_key stamped too.
    _seed_entity(migrated_driver, "e-koeln", "Koeln", "LOCATION", _vec(1.0))
    _await_vector(migrated_driver, "e-koeln", _vec(0.99, 0.01))
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'KOELN'})")
    _, method = resolve_alias_to_entity(
        migrated_driver, "KOELN", _vec(0.99, 0.01), cfg, entity_type="LOCATION", embed_model="m"
    )
    assert method == "vector_single"

    with migrated_driver.session() as s:
        rows = {
            r["sf"]: r["nk"]
            for r in s.run(
                "MATCH (a:Alias) WHERE a.surface_form IN ['Mainz','KOELN'] "
                "RETURN a.surface_form AS sf, a.norm_key AS nk"
            )
        }
    assert rows["Mainz"] == "mainz"
    assert rows["KOELN"] == "koeln"


def test_reresolve_to_different_entity_single_edge(migrated_driver: Driver) -> None:
    """Re-resolving an alias to a different entity leaves exactly one edge (#23).

    ``_write_resolved_to`` is last-writer-wins: a re-run that picks a different
    entity must drop the stale ``:RESOLVED_TO`` edge (not the old entity) and
    leave the alias with a single edge to the new entity.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    _seed_entity(migrated_driver, "e-old", "Old", "LOCATION", _vec(0.0, 0.0, 1.0))
    _seed_entity(migrated_driver, "e-new", "New", "LOCATION", _vec(1.0))
    _await_vector(migrated_driver, "e-new", _vec(0.99, 0.01))
    with migrated_driver.session() as s:
        s.run("MATCH (e:Entity {id:'e-old'}) MERGE (a:Alias {surface_form:'X'}) MERGE (a)-[:RESOLVED_TO]->(e)")

    eid, _ = resolve_alias_to_entity(
        migrated_driver, "X", _vec(0.99, 0.01), load_resolution_env(), entity_type="LOCATION", embed_model="m"
    )
    assert eid == "e-new"

    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (a:Alias {surface_form:'X'}) OPTIONAL MATCH (a)-[r:RESOLVED_TO]->(e) "
            "RETURN count(r) AS edges, collect(e.id) AS targets"
        ).single()
        old = s.run("MATCH (e:Entity {id:'e-old'}) RETURN count(e) AS n").single()
    assert rec is not None and rec["edges"] == 1
    assert rec["targets"] == ["e-new"]
    assert old is not None and old["n"] == 1  # the old entity survives; only its edge is dropped


def test_case_normalize_off_uses_raw_surface_as_norm_key(migrated_driver: Driver) -> None:
    """With case_normalize disabled, norm_key is the trimmed (un-casefolded) surface.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import ResolutionConfig

    cfg = ResolutionConfig(
        embed_cluster_threshold=0.86,
        llm_tiebreak_enabled=False,
        case_normalize=False,
        vector_k=5,
    )
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: '  Berlin  '})")
    resolve_alias_to_entity(migrated_driver, "  Berlin  ", _vec(0.3, 0.7), cfg, entity_type="LOCATION", embed_model="m")

    with migrated_driver.session() as s:
        rec = s.run("MATCH (a:Alias {surface_form:'  Berlin  '}) RETURN a.norm_key AS k").single()
    assert rec is not None and rec["k"] == "Berlin"  # trimmed, NOT casefolded
