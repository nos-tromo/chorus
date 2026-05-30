# Entity resolution stage (Alias → Entity) — design

**Date:** 2026-05-30
**Status:** approved (design); pending implementation plan
**Scope:** implement the resolution stage that clusters unresolved `:Alias`
nodes onto canonical `:Entity` nodes, completing the ingestion pipeline and
upgrading the already-shipped graph tools + agent from raw alias-matching to
real entity clustering.

## Context

Extraction writes `(:Post)-[:MENTIONS]->(:Alias {surface_form})`; the
`Alias → Entity` resolution stage is the last unimplemented part of the
ingestion pipeline. Today there are **no `:Entity` nodes and no `:RESOLVED_TO`
edges**, so "topics" in `topic_co_occurrence` / `authors_connected_by_topic` /
`author_activity_summary` / `posts_mentioning` are raw alias surface forms —
"Joe Biden" and "President Biden" don't cluster. Those tools already
`COALESCE` alias→entity via `RESOLVED_TO`, so **landing this stage upgrades them
(and the agent) with no tool changes** — the whole reason this was the
highest-leverage next step.

## Current scaffold (`chorus/ingestion/resolution.py`)

Intended pipeline (module docstring): `normalize_surface → lookup_alias →
cluster_candidates → llm_tiebreaker → mint`. Already implemented:

- `normalize_surface(s, cfg)` — strip + (config) casefold.
- `lookup_alias(driver, surface)` — exact `Alias-[:RESOLVED_TO]->Entity` cache.

Stubbed (`raise NotImplementedError`): `cluster_candidates`, `llm_tiebreaker`,
`resolve_alias_to_entity`.

`ResolutionConfig` (`load_resolution_env`): `embed_cluster_threshold=0.86`
(`RES_EMBED_THRESHOLD`), `llm_tiebreak_enabled=True` (`RES_LLM_TIEBREAK`),
`case_normalize=True` (`RES_CASE_NORMALIZE`). The `entity_embedding` vector
index (migration 003), `entity_id` / `alias_surface` uniqueness constraints
(001), `entity_canonical` index (002), `EMBED_DIM`, and `provider.embed` all
already exist. **No new migration is required** (alias `label`, entity props,
and `RESOLVED_TO` props are schema-free additions; finding unresolved aliases is
a bounded scan).

## Approved decisions

- **Batch, re-runnable** execution (a CLI stage), not inline-per-post.
- **Incremental accretion** (resolve each unresolved alias against the existing
  entity set; mint when no match) — not global all-pairs clustering. Clusters
  emerge as similar aliases land on the same entity.
- **Persist the GLiNER label** so `:Entity.type` is populated and candidate
  matching is restricted to the same type (a PERSON alias won't merge into a
  LOCATION entity).

## Design

### Per-alias pipeline

For each unresolved `:Alias` (processed in descending mention-count order, so
the most common surface form mints first and becomes the canonical name):

1. `lookup_alias` cache hit → already resolved, skip (idempotent).
2. Embed the alias `surface_form` (a short *name* embedding via `provider.embed`
   — unrelated to the deferred `Post.embedding`).
3. `cluster_candidates`: `CALL db.index.vector.queryNodes('entity_embedding', n, vec)`
   over-fetching (e.g. `max(4*k, 20)`), then filter to `score ≥ embed_cluster_threshold`
   **and same `type`** (the alias's label), returning the top `k` as
   `{id, canonical_name, type, score}` descending by score.
4. Decide:
   - **0 candidates** → `mint_entity` (new `:Entity`), `method="minted"`.
   - **1 candidate** → attach, `method="vector_single"`.
   - **>1 candidates** → `llm_tiebreaker`: if it returns a candidate id, attach
     (`method="vector_llm"`); if it abstains/parses to none, **or** tie-break is
     disabled, attach to the top-score candidate (`method="vector_topk"`) — never
     mint a third near-duplicate when matches already exist above threshold.
5. Write `(:Alias)-[:RESOLVED_TO {method, score, embed_model, resolved_at}]->(:Entity)`.
   **Commit per alias** so a freshly-minted entity is visible to the
   `entity_embedding` index for subsequent aliases in the same run.

### Function surface (`resolution.py`)

Refine the stub signatures (they were placeholders):

- `cluster_candidates(driver, embedding, threshold, *, k=5, entity_type=None) -> list[dict[str, Any]]`
  — returns `{id, canonical_name, type, score}`; over-fetch then type/threshold filter.
- `llm_tiebreaker(surface, candidates) -> str | None` — builds a prompt listing
  candidate `{id, canonical_name, type}`, asks the model (`provider.chat`,
  `TEXT_MODEL`) to reply with exactly one candidate id or `NONE`; strict parse
  (response must contain exactly one known candidate id, else `None`).
- `mint_entity(driver, surface, embedding, *, entity_type=None) -> str` — `CREATE`
  `:Entity {id: uuid4, canonical_name: surface, type: entity_type, embedding, description: null}`;
  returns the id.
- `resolve_alias_to_entity(driver, surface, embedding, cfg, *, entity_type=None) -> str`
  — orchestrates steps 1–5; returns the entity id.
- `resolve_all(driver, cfg) -> ResolutionSummary` — the batch runner: fetch
  unresolved aliases (`MATCH (a:Alias) WHERE NOT (a)-[:RESOLVED_TO]->(:Entity)`,
  ordered by mention count, returning `surface_form` + `label`), batch-embed the
  surface forms via `provider.embed` (chunked), then `resolve_alias_to_entity`
  each with a per-alias commit. Returns counts
  (`processed, attached_single, attached_llm, attached_topk, minted, skipped`)
  and logs progress (loguru). Idempotent / re-runnable.

`ResolutionSummary` is a small frozen dataclass of the counts.

### Ingestion change (`extraction.py`)

`write_mentions`: `MERGE (al:Alias {surface_form: span.surface_form}) ON CREATE
SET al.label = span.label`. The span already carries `label` from GLiNER; this
is the only ingestion change. (First-seen label wins; sufficient for v1.)

Aliases created **before** this change have no `label`: they resolve with
`entity_type=None` (no type filter, untyped minted entity) — a graceful
degradation, not an error. Re-running extraction would type them.

### CLI (`chorus/ingestion/cli.py`)

Add a `resolve` subcommand: build the driver, load `ResolutionConfig`, run
`resolve_all`, print the summary. Mirrors the existing ingest CLI patterns
(driver/config wiring, exit codes). Operator flow: `… ingest` then `… resolve`.

### Config (`env_cfg.py`)

Optionally add `RES_VECTOR_K` (default 5) to `ResolutionConfig` for the
candidate fan-out; thresholds stay config, not constants (already honored).

### Effect on existing tools

None required. `posts_mentioning`, `topic_co_occurrence`,
`authors_connected_by_topic`, and `author_activity_summary` already resolve
`MENTIONS → Alias -[:RESOLVED_TO]-> Entity` via `COALESCE`, so after a resolve
run they surface entity ids + canonical names and cluster correctly. An
integration test will assert this end-to-end.

## Testing

Unit (stub `provider.embed` with controlled vectors so cosine is deterministic;
stub `provider.chat` for tie-break):

- `cluster_candidates`: respects threshold; same-type filter excludes
  other-type entities; orders by score.
- `llm_tiebreaker`: parses a chosen id; returns `None` on `NONE`/unparseable.
- `resolve_alias_to_entity`: 0→mint (entity created with `type`, `RESOLVED_TO`
  written with `method="minted"`); 1→attach; >1→LLM path; cache hit→idempotent.
- `resolve_all`: two similar same-type aliases resolve to **one** entity
  (clustering emerges); a different-type / dissimilar alias mints its own;
  re-run is a no-op; counts are correct.
- `extraction`: `write_mentions` stores `al.label`.

Integration (ephemeral Neo4j via `migrated_driver`): seed posts + aliases, run
`resolve_all`, then call `topic_co_occurrence` (or `posts_mentioning`) and assert
the two aliases now report the same `entity_id` / canonical name.

## Out of scope (v1)

- `Post.embedding` / `semantic_search` (separate round).
- Un-merge / re-resolution tooling (RESOLVED_TO is reversible by design, but no
  UI/CLI to split or re-run with cleared edges yet).
- LLM-generated `:Entity.description` (left null).
- Cross-type merges; alias-label refinement beyond first-seen.

## Resolved decisions (defaults baked in)

- Execution: batch CLI stage, re-runnable.
- Clustering: incremental accretion against the existing entity set.
- Label persisted on `:Alias` (first-seen) → `:Entity.type` + same-type matching.
- Multi-candidate LLM abstain → attach to top-score (never fragment).
- Mint only when **zero** same-type candidates clear the threshold.
- `canonical_name` = the (most-mentioned) alias surface form; `description` null.
- `RESOLVED_TO` carries `method` / `score` / `embed_model` / `resolved_at` for
  audit + reversibility.
