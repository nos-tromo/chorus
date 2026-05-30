# Entity Resolution Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Integration tests need Docker (testcontainers → Neo4j 5.26). **Stage new files (`git add`) before running pre-commit** — `--all-files` only checks tracked files.

**Goal:** Implement the `Alias → Entity` resolution stage — a batch, re-runnable pass that clusters unresolved `:Alias` nodes onto canonical `:Entity` nodes via the `entity_embedding` vector index (+ same-type filter + LLM tie-break, minting when no match), upgrading the already-shipped graph tools/agent from raw-alias to entity clustering.

**Architecture:** Implement the stubbed functions in `chorus/ingestion/resolution.py` (`cluster_candidates`, `llm_tiebreaker`, `resolve_alias_to_entity`) plus new `mint_entity` / `resolve_all` / `ResolutionSummary`; persist the GLiNER label on `:Alias` (`extraction.py`); add a `resolve` CLI subcommand. Incremental accretion: each unresolved alias attaches to a matched entity or mints a new one; per-alias commit + an in-run normalized cache make clustering deterministic. No tool changes (tools already `COALESCE` alias→entity). No migration.

**Tech Stack:** Python 3.12, Neo4j (Cypher + `db.index.vector.queryNodes`), `provider.embed`/`provider.chat` via LiteLLM, Pydantic/dataclasses, pytest + testcontainers, ruff, mypy. `uv`.

**Approved spec:** `docs/superpowers/specs/2026-05-30-entity-resolution-design.md`.

---

## Executor notes (read first)

- **Neo4j vector indexes are eventually consistent.** After creating/minting an `:Entity` with an `embedding`, it may not be returned by `db.index.vector.queryNodes` for a short moment. Therefore: (a) tests **poll** until the entity is searchable (`_await_vector` helper below) rather than assuming immediate visibility; (b) production `resolve_all` keeps an **in-run normalized cache** so case-variant aliases ("Berlin"/"berlin") cluster deterministically regardless of index lag. Residual risk (semantically-similar but not normalize-equal aliases minted as duplicates under lag) is accepted for v1 and reconciled by re-running; flag it if the live smoke shows it.
- Test embeddings must be `EMBED_DIM` (=1024) long. Use the `_vec(*head)` helper (pads with zeros). The vector index's cosine `score` is what `RES_EMBED_THRESHOLD` is compared against; the test vectors are chosen far apart so the exact score mapping doesn't matter.
- `resolution.py` and `extraction.py` are already in `tests/conftest.py::_CHORUS_ENV_MODULES`; no conftest change needed.

## File structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `chorus/ingestion/extraction.py` | persist `al.label` on the Alias MERGE |
| Modify | `chorus/utils/env_cfg.py` | add `vector_k` to `ResolutionConfig` + loader |
| Modify | `chorus/ingestion/resolution.py` | implement `cluster_candidates`, `mint_entity`, `llm_tiebreaker`, `resolve_alias_to_entity`, `_write_resolved_to`, `resolve_all`, `ResolutionSummary` |
| Modify | `chorus/ingestion/cli.py` | add the `resolve` subcommand |
| Create | `tests/ingestion/test_extraction.py` | label persistence |
| Modify | `tests/utils/test_env_cfg.py` | `vector_k` config test |
| Create | `tests/ingestion/test_resolution.py` | unit/integration for the resolution functions |
| Modify | `tests/ingestion/test_cli.py` | `resolve` subcommand test |
| Create | `tests/integration/test_resolution_e2e.py` | resolve_all → downstream tool clusters |

---

## Task 1: Persist the GLiNER label on `:Alias`

**Files:** Modify `chorus/ingestion/extraction.py`; Create `tests/ingestion/test_extraction.py`

- [ ] **Step 1 — failing test** (`tests/ingestion/test_extraction.py`)

```python
"""extraction.write_mentions persists the GLiNER label on the Alias."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_write_mentions_stores_alias_label(migrated_driver: Driver) -> None:
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
```

- [ ] **Step 2 — run, expect fail:** `uv run pytest tests/ingestion/test_extraction.py -v` → `a.label` is `None`.

- [ ] **Step 3 — implement:** in `chorus/ingestion/extraction.py::write_mentions`, add `ON CREATE SET` to the Alias MERGE:

```cypher
    MERGE (al:Alias {surface_form: span.surface_form})
      ON CREATE SET al.label = span.label
    MERGE (p)-[m:MENTIONS]->(al)
```

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit** (stage first): `feat(ingestion): persist GLiNER label on :Alias for resolution`

---

## Task 2: `ResolutionConfig.vector_k`

**Files:** Modify `chorus/utils/env_cfg.py`; Modify `tests/utils/test_env_cfg.py`

- [ ] **Step 1 — failing test** (append to `tests/utils/test_env_cfg.py`)

```python
def test_resolution_config_vector_k(monkeypatch: "pytest.MonkeyPatch") -> None:
    from chorus.utils.env_cfg import load_resolution_env

    monkeypatch.delenv("RES_VECTOR_K", raising=False)
    assert load_resolution_env().vector_k == 5
    monkeypatch.setenv("RES_VECTOR_K", "8")
    assert load_resolution_env().vector_k == 8
```

(Ensure `import pytest` is present at the top of the file.)

- [ ] **Step 2 — run, expect fail:** `AttributeError: 'ResolutionConfig' object has no attribute 'vector_k'`.

- [ ] **Step 3 — implement:** add the field to `ResolutionConfig` and the loader:

```python
# in class ResolutionConfig (after case_normalize):
    vector_k: int

# in load_resolution_env(), inside ResolutionConfig(...):
        vector_k=_env_int("RES_VECTOR_K", 5),
```

Also add to the `ResolutionConfig` docstring Attributes: `vector_k: Candidate fan-out for the entity vector search.`

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `feat(config): add RES_VECTOR_K to ResolutionConfig`

---

## Task 3: `cluster_candidates` (vector search + type filter)

**Files:** Modify `chorus/ingestion/resolution.py`; Create `tests/ingestion/test_resolution.py`

- [ ] **Step 1 — failing test** (`tests/ingestion/test_resolution.py`) — includes shared helpers used by later tasks.

```python
"""Entity-resolution pipeline tests (vector search, mint, tie-break, batch)."""

from __future__ import annotations

import time
from typing import Any

from neo4j import Driver

EMBED_DIM = 1024


def _vec(*head: float) -> list[float]:
    """A 1024-d vector with the given leading components, zero-padded."""
    v = [0.0] * EMBED_DIM
    for i, x in enumerate(head):
        v[i] = float(x)
    return v


def _await_vector(driver: Driver, expected_id: str, query: list[float], tries: int = 50) -> None:
    """Poll the vector index until `expected_id` is searchable (index lag)."""
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
    assert cands[0]["canonical_name"] == "Berlin" and cands[0]["type"] == "LOCATION"
```

- [ ] **Step 2 — run, expect fail:** `NotImplementedError`.

- [ ] **Step 3 — implement** `cluster_candidates` in `resolution.py` (replace the stub body; change the return type to `list[dict[str, Any]]` and add `entity_type`):

```python
def cluster_candidates(
    driver: Driver,
    embedding: list[float],
    threshold: float,
    *,
    k: int = 5,
    entity_type: str | None = None,
) -> list[dict[str, Any]]:
    """Find candidate entities by vector similarity, filtered to one type.

    Over-fetches from the vector index then filters to ``score >= threshold``
    and (when given) ``type == entity_type``, returning the top ``k`` as
    ``{id, canonical_name, type, score}`` descending by score.
    """
    fetch = max(4 * k, 20)
    cypher = """
    CALL db.index.vector.queryNodes('entity_embedding', $fetch, $embedding)
      YIELD node, score
    WHERE score >= $threshold
      AND ($entity_type IS NULL OR node.type = $entity_type)
    RETURN node.id AS id, node.canonical_name AS canonical_name,
           node.type AS type, score
    ORDER BY score DESC
    LIMIT $k
    """
    with driver.session() as session:
        rows = session.run(
            cypher,
            fetch=fetch,
            embedding=embedding,
            threshold=threshold,
            entity_type=entity_type,
            k=k,
        ).data()
    return [
        {"id": r["id"], "canonical_name": r["canonical_name"], "type": r["type"], "score": r["score"]}
        for r in rows
    ]
```

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `feat(resolution): implement cluster_candidates (vector + type filter)`

---

## Task 4: `mint_entity`

**Files:** Modify `chorus/ingestion/resolution.py`; Modify `tests/ingestion/test_resolution.py`

- [ ] **Step 1 — failing test** (append)

```python
def test_mint_entity_creates_typed_entity(migrated_driver: Driver) -> None:
    from chorus.ingestion.resolution import mint_entity

    eid = mint_entity(migrated_driver, "Bratwurst", _vec(0.5, 0.5), entity_type="FOOD")
    assert eid
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (e:Entity {id: $id}) RETURN e.canonical_name AS n, e.type AS t, e.description AS d",
            id=eid,
        ).single()
    assert rec["n"] == "Bratwurst"
    assert rec["t"] == "FOOD"
    assert rec["d"] is None
```

- [ ] **Step 2 — run, expect fail** (ImportError / no such function).

- [ ] **Step 3 — implement** (add to `resolution.py`; add `import uuid` at top):

```python
def mint_entity(
    driver: Driver,
    surface: str,
    embedding: list[float],
    *,
    entity_type: str | None = None,
) -> str:
    """Create a new :Entity from an unresolved alias and return its id."""
    entity_id = str(uuid.uuid4())
    cypher = """
    CREATE (e:Entity {
        id: $id, canonical_name: $surface, type: $entity_type,
        embedding: $embedding, description: null
    })
    """
    with driver.session() as session:
        session.run(
            cypher, id=entity_id, surface=surface, entity_type=entity_type, embedding=embedding
        ).consume()
    return entity_id
```

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `feat(resolution): implement mint_entity`

---

## Task 5: `llm_tiebreaker`

**Files:** Modify `chorus/ingestion/resolution.py`; Modify `tests/ingestion/test_resolution.py`

- [ ] **Step 1 — failing test** (append; stubs `provider.chat`)

```python
def test_llm_tiebreaker_picks_and_abstains(monkeypatch: "pytest.MonkeyPatch") -> None:
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

    # ambiguous / unparseable -> None
    monkeypatch.setattr(provider, "chat", lambda messages, **kw: "e-1 or maybe e-2")
    assert llm_tiebreaker("President Biden", candidates) is None
```

(Add `import pytest` to the test file's imports.)

- [ ] **Step 2 — run, expect fail** (`NotImplementedError`).

- [ ] **Step 3 — implement** (replace stub; keep the `surface, candidates` signature):

```python
def llm_tiebreaker(surface: str, candidates: list[dict[str, Any]]) -> str | None:
    """Pick the candidate entity that matches ``surface`` via an LLM call.

    Returns the chosen entity id, or ``None`` to signal "no confident match"
    (the caller then falls back to the top-score candidate). The response must
    contain exactly one known candidate id; otherwise the result is ``None``.
    """
    from chorus.inference import provider

    lines = [
        f"{i + 1}. id={c['id']} name={c['canonical_name']!r} type={c['type']}"
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "You are resolving an entity reference to a canonical entity.\n"
        f"Surface form: {surface!r}\n"
        "Candidates:\n" + "\n".join(lines) + "\n\n"
        "Reply with ONLY the id of the candidate that refers to the same "
        "real-world entity as the surface form, or NONE if none of them do."
    )
    text = provider.chat([{"role": "user", "content": prompt}]).strip()
    matched = [c["id"] for c in candidates if c["id"] in text]
    return matched[0] if len(matched) == 1 else None
```

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `feat(resolution): implement llm_tiebreaker`

---

## Task 6: `resolve_alias_to_entity` + `_write_resolved_to`

**Files:** Modify `chorus/ingestion/resolution.py`; Modify `tests/ingestion/test_resolution.py`

- [ ] **Step 1 — failing tests** (append; cover mint / single / cache-idempotent paths)

```python
def test_resolve_alias_mints_when_no_candidates(migrated_driver: Driver) -> None:
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Solingen'})")
    cfg = load_resolution_env()
    eid, method = resolve_alias_to_entity(
        migrated_driver, "Solingen", _vec(0.3, 0.7), cfg, entity_type="LOCATION", embed_model="bge-m3"
    )
    assert method == "minted"
    with migrated_driver.session() as s:
        rec = s.run(
            "MATCH (a:Alias {surface_form: 'Solingen'})-[r:RESOLVED_TO]->(e:Entity {id: $id}) "
            "RETURN r.method AS m, e.type AS t",
            id=eid,
        ).single()
    assert rec["m"] == "minted"
    assert rec["t"] == "LOCATION"


def test_resolve_alias_attaches_to_single_candidate(migrated_driver: Driver) -> None:
    from chorus.ingestion.resolution import resolve_alias_to_entity
    from chorus.utils.env_cfg import load_resolution_env

    _seed_entity(migrated_driver, "e-berlin", "Berlin", "LOCATION", _vec(1.0))
    _await_vector(migrated_driver, "e-berlin", _vec(0.99, 0.01))
    # the Alias node must exist (extraction normally creates it)
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Berlin '})")  # trailing space variant

    eid, method = resolve_alias_to_entity(
        migrated_driver, "Berlin ", _vec(0.99, 0.01), load_resolution_env(),
        entity_type="LOCATION", embed_model="bge-m3",
    )
    assert eid == "e-berlin"
    assert method == "vector_single"


def test_resolve_alias_is_idempotent(migrated_driver: Driver) -> None:
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
```

- [ ] **Step 2 — run, expect fail** (`NotImplementedError`).

- [ ] **Step 3 — implement** (replace `resolve_alias_to_entity` stub; add `_write_resolved_to`). Uses the existing `lookup_alias`:

```python
def resolve_alias_to_entity(
    driver: Driver,
    surface: str,
    embedding: list[float],
    cfg: ResolutionConfig,
    *,
    entity_type: str | None = None,
    embed_model: str = "",
) -> tuple[str, str]:
    """Resolve one alias to an entity id; returns ``(entity_id, method)``.

    Pipeline: existing-resolution cache → vector candidates → decide
    (0 mint / 1 attach / >1 LLM-or-top-score) → write RESOLVED_TO.
    """
    existing = lookup_alias(driver, surface)
    if existing is not None:
        return existing, "skipped"

    candidates = cluster_candidates(
        driver, embedding, cfg.embed_cluster_threshold, k=cfg.vector_k, entity_type=entity_type
    )
    if not candidates:
        entity_id = mint_entity(driver, surface, embedding, entity_type=entity_type)
        method, score = "minted", None
    elif len(candidates) == 1:
        entity_id, method, score = candidates[0]["id"], "vector_single", candidates[0]["score"]
    else:
        chosen = llm_tiebreaker(surface, candidates) if cfg.llm_tiebreak_enabled else None
        if chosen is not None:
            entity_id, method = chosen, "vector_llm"
            score = next((c["score"] for c in candidates if c["id"] == chosen), None)
        else:
            entity_id, method, score = candidates[0]["id"], "vector_topk", candidates[0]["score"]

    _write_resolved_to(driver, surface, entity_id, method=method, score=score, embed_model=embed_model)
    return entity_id, method


def _write_resolved_to(
    driver: Driver,
    surface: str,
    entity_id: str,
    *,
    method: str,
    score: float | None,
    embed_model: str,
) -> None:
    """MERGE the RESOLVED_TO edge with provenance (idempotent)."""
    cypher = """
    MATCH (a:Alias {surface_form: $surface})
    MATCH (e:Entity {id: $entity_id})
    MERGE (a)-[r:RESOLVED_TO]->(e)
    SET r.method = $method, r.score = $score,
        r.embed_model = $embed_model, r.resolved_at = datetime()
    """
    with driver.session() as session:
        session.run(
            cypher,
            surface=surface,
            entity_id=entity_id,
            method=method,
            score=score,
            embed_model=embed_model,
        ).consume()
```

Note: `test_resolve_alias_attaches_to_single_candidate` pre-creates the `:Alias` node because `_write_resolved_to` `MATCH`es it (extraction creates it in the real pipeline). `mint_entity` paths also need the alias node — see the same pattern; the mint tests create the alias via the resolve call? No — add `MERGE (:Alias {surface_form: surface})` is NOT done here. **Important:** `resolve_alias_to_entity` assumes the `:Alias` already exists (it always does post-extraction). The mint tests above must first create the alias node. Update the two mint/idempotent tests to `MERGE (:Alias {surface_form: '<surface>'})` before calling (add that line to each).

- [ ] **Step 4 — run, expect pass** (add the missing `MERGE (:Alias …)` seeds flagged above if any test errors on a missing alias).

- [ ] **Step 5 — commit:** `feat(resolution): implement resolve_alias_to_entity + RESOLVED_TO provenance`

---

## Task 7: `resolve_all` + `ResolutionSummary`

**Files:** Modify `chorus/ingestion/resolution.py`; Modify `tests/ingestion/test_resolution.py`

- [ ] **Step 1 — failing test** (append; stubs `provider.embed` with controlled vectors keyed by surface form)

```python
def test_resolve_all_clusters_and_is_rerunnable(
    migrated_driver: Driver, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.utils.env_cfg import load_resolution_env

    # Two case-variant LOCATION aliases + one distinct PERSON alias, each on a post.
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
        n_entities = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
        # both Berlin variants resolve to the same entity
        same = s.run(
            "MATCH (:Alias {surface_form:'Berlin'})-[:RESOLVED_TO]->(e1), "
            "(:Alias {surface_form:'berlin'})-[:RESOLVED_TO]->(e2) "
            "RETURN e1.id = e2.id AS same"
        ).single()["same"]
    assert n_entities == 2
    assert same is True

    # re-run is a no-op (everything already resolved)
    again = resolve_all(migrated_driver, load_resolution_env())
    assert again.processed == 0
```

- [ ] **Step 2 — run, expect fail** (ImportError).

- [ ] **Step 3 — implement** (add to `resolution.py`; add `from dataclasses import dataclass, asdict`, `from loguru import logger`, `from chorus.inference import provider`, `from chorus.utils.env_cfg import load_inference_env`):

```python
@dataclass(frozen=True)
class ResolutionSummary:
    """Counts from a resolve_all run."""

    processed: int = 0
    attached_single: int = 0
    attached_llm: int = 0
    attached_topk: int = 0
    attached_cache: int = 0
    minted: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


_METHOD_FIELD = {
    "vector_single": "attached_single",
    "vector_llm": "attached_llm",
    "vector_topk": "attached_topk",
    "run_cache": "attached_cache",
    "minted": "minted",
    "skipped": "skipped",
}


def _embed_in_chunks(surfaces: list[str], *, chunk: int = 128) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(surfaces), chunk):
        out.extend(provider.embed(surfaces[i : i + chunk]))
    return out


def resolve_all(driver: Driver, cfg: ResolutionConfig) -> ResolutionSummary:
    """Resolve every unresolved :Alias to a canonical :Entity (batch, idempotent).

    Processes aliases most-mentioned first (so the common surface form mints and
    becomes the canonical name). An in-run normalized cache makes case-variant
    aliases cluster deterministically despite vector-index lag.
    """
    fetch = """
    MATCH (a:Alias) WHERE NOT (a)-[:RESOLVED_TO]->(:Entity)
    OPTIONAL MATCH (a)<-[:MENTIONS]-(p:Post)
    WITH a, count(p) AS mentions
    ORDER BY mentions DESC, a.surface_form ASC
    RETURN a.surface_form AS surface_form, a.label AS label
    """
    with driver.session() as session:
        aliases = [(r["surface_form"], r["label"]) for r in session.run(fetch)]
    if not aliases:
        return ResolutionSummary()

    embed_model = load_inference_env().embed_model
    vectors = _embed_in_chunks([a[0] for a in aliases])

    counts = dict.fromkeys(_METHOD_FIELD.values(), 0)
    counts["processed"] = 0
    run_cache: dict[str, str] = {}

    for (surface, label), vec in zip(aliases, vectors, strict=True):
        norm = normalize_surface(surface, cfg)
        if norm in run_cache:
            _write_resolved_to(
                driver, surface, run_cache[norm], method="run_cache", score=None, embed_model=embed_model
            )
            counts["attached_cache"] += 1
        else:
            entity_id, method = resolve_alias_to_entity(
                driver, surface, vec, cfg, entity_type=label, embed_model=embed_model
            )
            run_cache[norm] = entity_id
            counts[_METHOD_FIELD[method]] += 1
        counts["processed"] += 1

    logger.info("resolution complete: {}", counts)
    return ResolutionSummary(**counts)
```

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `feat(resolution): implement resolve_all batch runner + ResolutionSummary`

---

## Task 8: `resolve` CLI subcommand

**Files:** Modify `chorus/ingestion/cli.py`; Modify `tests/ingestion/test_cli.py`

- [ ] **Step 1 — failing test** (append to `tests/ingestion/test_cli.py`; mirror its existing style — stub `provider.embed`, run over a seeded graph, assert exit 0 + JSON summary)

```python
def test_cli_resolve(migrated_driver, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from chorus.inference import provider
    from chorus.ingestion.cli import main

    with migrated_driver.session() as s:
        s.run("MERGE (a:Alias {surface_form: 'Trier'}) ON CREATE SET a.label = 'LOCATION'")
    # empty entity index -> zero candidates -> mint; zero-vector is fine here.
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [[0.0] * 1024 for _ in texts])

    rc = main(["resolve"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "processed: 1" in out
    assert "minted: 1" in out
```

(`main(["resolve"])` opens its own driver via `get_driver()` against the test
container and closes it in `finally` — the same lifecycle the existing `run`
CLI test relies on. Output is plain `field: count` lines, matching the `run`
command's style.)

- [ ] **Step 2 — run, expect fail** (`invalid choice: 'resolve'`).

- [ ] **Step 3 — implement** in `chorus/ingestion/cli.py`. The real CLI builds the
parser inline in `main()` with a flat `if args.cmd == "<name>"` dispatch (subcommands
`run`; `dest="cmd"`), prints plain text, and returns `2` on unknown command. Match that:

```python
# 1) extend the env_cfg import to add load_resolution_env, and import resolve_all:
from chorus.ingestion.resolution import resolve_all
from chorus.utils.env_cfg import (
    load_ingestion_env,
    load_path_env,
    load_resolution_env,
    load_retention_env,
)

# 2) register the subcommand (after the `run` subparser is added in main()):
    sub.add_parser("resolve", help="resolve unresolved aliases to entities")

# 3) handle it (add before the final `return 2`), mirroring the `run` branch's
#    open-driver / try-finally-close style and plain-text output:
    if args.cmd == "resolve":
        driver = get_driver()
        try:
            summary = resolve_all(driver, load_resolution_env())
        finally:
            close_driver()
        for field, count in summary.as_dict().items():
            print(f"{field}: {count}")
        return 0
```

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `feat(cli): add 'resolve' subcommand for the resolution stage`

---

## Task 9: End-to-end — a downstream tool clusters after resolution

**Files:** Create `tests/integration/test_resolution_e2e.py`

- [ ] **Step 1 — failing test** — seed two case-variant aliases co-mentioned with a third topic, resolve, then assert `topic_co_occurrence` treats the variants as one entity.

```python
"""After resolve_all, the graph tools cluster aliases by their resolved entity."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def _vec(*head: float) -> list[float]:
    v = [0.0] * 1024
    for i, x in enumerate(head):
        v[i] = float(x)
    return v


def test_resolution_lets_topic_tool_cluster(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    from chorus.inference import provider
    from chorus.ingestion.resolution import resolve_all
    from chorus.tools.topic_co_occurrence import TopicCoOccurrenceIn, topic_co_occurrence
    from chorus.utils.env_cfg import load_resolution_env

    # Post 1 mentions "Berlin" + "Spree"; Post 2 mentions "berlin" + "Spree".
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

    resolve_all(migrated_driver, load_resolution_env())

    # "Spree" co-occurs with the single Berlin *entity* across both posts (count 2),
    # not two separate alias surface forms.
    out = topic_co_occurrence(
        migrated_driver, TopicCoOccurrenceIn(topic="Spree", limit=10),
        user="t", audit=in_memory_audit,
    )
    berlin = [c for c in out.cooccurring if c.entity_id is not None and c.topic == "Berlin"]
    assert len(berlin) == 1
    assert berlin[0].count == 2  # both posts, via the one resolved entity
```

(If `topic_co_occurrence`'s canonical name differs from `"Berlin"` because the minted entity took whichever variant sorted first by mention count, assert on `entity_id` uniqueness + `count == 2` instead of the exact name.)

- [ ] **Step 2 — run, expect fail** (before the tools see entities, the variants are two alias topics).

- [ ] **Step 3 — implement:** nothing new — this validates Tasks 1–8 compose. If it fails, fix the underlying function, not the test.

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `test(resolution): end-to-end clustering through topic_co_occurrence`

---

## Final verification

- [ ] `uv run pytest` — full suite green (existing + new resolution tests).
- [ ] `uv run pre-commit run --all-files` — ruff + ruff-format + mypy clean (stage new files first; use `cast(...)` for any `Any`→typed returns as needed).
- [ ] **Live smoke** (the value check + the eventual-consistency validation): on the Ollama/data-plane stack, `python -m chorus.ingestion.cli run` then `python -m chorus.ingestion.cli resolve`; confirm `:Entity` nodes + `RESOLVED_TO` edges appear, then ask the agent "which authors are connected to X by topic" / use `topic_co_occurrence` and confirm variants now cluster. Watch for duplicate entities from index lag on semantically-similar (not normalize-equal) aliases — if present, that's the documented residual risk (re-run / future merge pass).

## Notes for the executor

- Cypher comments cite the design; keep the per-alias commit semantics (each `resolve_alias_to_entity` / `_write_resolved_to` / `mint_entity` opens its own session, so writes commit before the next alias queries).
- No new migration: `entity_embedding`, `entity_id`/`alias_surface` constraints, `EMBED_DIM` all exist.
- No retrieval-tool changes — they already `COALESCE` alias→entity; Task 9 proves it.
- Build order is the task order (1→9); each is independently committable, and the integration test (9) is the capstone.
