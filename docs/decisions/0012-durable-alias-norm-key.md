# 0012 — Durable alias `norm_key` for cross-run resolution

Status: accepted
Date: 2026-06-02

## Context

Entity resolution collapses case/whitespace variants of a surface form
(`Berlin`, `berlin`, `Berlin `) onto one canonical `:Entity`. The original
implementation did this with an in-memory `run_cache` keyed on
`(normalize_surface(surface), label)` inside `resolve_all`. That cache only
lives for one run, so under incremental ingestion the collapse was not durable:
`Berlin` resolved on Monday and `berlin` ingested on Tuesday started a fresh run
with an empty cache, and if their embeddings fell below the vector threshold,
`berlin` minted a **duplicate** entity (issue #24).

Two adjacent defects compounded it:

- The per-alias `lookup_alias` (exact `surface_form` match) was **dead** in the
  batch path — `resolve_all`'s fetch already excludes resolved aliases, so the
  lookup always returned `None`, costing one Neo4j round-trip per alias for
  nothing (issue #25).
- `_write_resolved_to` MERGEd on `(alias, entity)`, so re-resolving an alias to
  a *different* entity (manual fix, threshold change, concurrent run) created a
  **second** `:RESOLVED_TO` edge. Neo4j CE has no relationship-cardinality
  constraint to prevent it (issue #23, write-side).

## Decision

Persist the normalized key on the alias and resolve against it durably.

1. **`:Alias.norm_key`, owned by resolution (not extraction).** The key is
   `normalize_surface(surface, cfg)` and is stamped on the alias when
   resolution writes the `:RESOLVED_TO` edge (both `mint_entity` and
   `_write_resolved_to`, in the same atomic statement). `extraction.py` /
   `write_mentions` is untouched and the raw `surface_form` (with its UNIQUE
   `alias_surface` constraint) is preserved for reversibility.
2. **Durable lookup replaces the dead one.** `lookup_alias` →
   `lookup_resolved_norm_key(driver, norm_key, label)`, which finds the entity
   an already-resolved alias with the same `(norm_key, label)` points to. This
   does the #24 cross-run dedup and is the cheap #25 win in one move — the
   former dead round-trip now does real work. A null-safe label predicate
   mirrors `cluster_candidates`, so a PERSON `Apple` never collapses into a
   FOOD `apple`.
3. **Range index, not a constraint** (migration `004_alias_norm_key`). Many raw
   `surface_form` values share one `norm_key`, so uniqueness would reject the
   second variant and break ingestion.
4. **Backfill: Python CLI primary, lazy floor.**
   `python -m chorus.ingestion.cli backfill-norm-keys` stamps `norm_key` on
   pre-change resolved aliases, computing it with the same Python
   `normalize_surface`. Lazy stamping on next resolve is the floor.
5. **Cardinality guard, last-writer-wins.** `_write_resolved_to` deletes any
   existing edge to a *different* entity before the MERGE, leaving exactly one
   `:RESOLVED_TO` per alias; re-resolution to the same entity is idempotent.
6. **`run_cache` retained** as a within-run zero-round-trip fast path (precedence
   `run_cache` → `lookup_resolved_norm_key` → `cluster_candidates` → tie-break →
   mint). A new method `cross_run` / `ResolutionSummary.attached_cross_run` is
   added; `skipped` is retired (kept on the dataclass for compatibility).

## Consequences

- Case/whitespace variants collapse to one entity across runs, not just within
  a run; the headline #24 duplicate-minting path is closed.
- One dead Neo4j round-trip per alias removed (#25's cheap win); the larger
  `resolve_all` restructure (keyset pagination, UNWIND-batched writes, streaming
  embeddings) is deliberately **deferred** — the per-alias mint+commit is
  load-bearing sequential (clustering for alias *j* must see entities minted for
  aliases *<j* via the vector index), and the issue itself asks for
  evidence-driven optimization.
- An alias ends with exactly one `:RESOLVED_TO` edge under the supported
  single-writer batch CLI. **Residual:** Neo4j CE cannot enforce relationship
  cardinality, so two *concurrent* `resolve_all` processes could still produce a
  transient second edge; the next run's guard collapses it, and
  `posts_mentioning`'s per-post aggregation (PR for #23 query-side) is the
  read-side backstop.
- **Breaking for existing graph data**: aliases resolved before this change have
  no `norm_key`. Acceptable in early development (wipe + re-ingest, or run the
  backfill). Lazy stamping alone leaves a window where a new variant mints a
  duplicate before the old alias is re-touched — the backfill closes it.
- Flipping `RES_CASE_NORMALIZE` changes the key space; re-running `resolve`
  re-derives keys from the current config, but stale `norm_key`s from the prior
  policy persist until then. Document if the toggle is ever changed in anger.

## Alternatives considered

- **Compute `norm_key` at `write_mentions` time.** Rejected: it couples the
  inline ingestion pass to a resolution-stage toggle (`RES_CASE_NORMALIZE`) and
  freezes the casefold policy at ingestion, so aliases written before/after a
  toggle change would disagree. Resolution owns normalization; it re-derives the
  key from the current config on every run.
- **Cypher `toLower(trim())` backfill inside the migration.** Rejected as the
  primary mechanism: `str.casefold()` ≠ Cypher `toLower()` for non-ASCII — German
  `ß` casefolds to `ss` but is unchanged by `toLower` — so it would mint keys
  that silently disagree with live resolution exactly on the names where dedup
  matters. Migrations are also pure auto-commit Cypher and cannot call Python.
  Acceptable only as a documented degraded fallback for operators who accept
  ASCII-equivalence.
- **Keep-first cardinality.** Rejected: it would freeze the first (possibly
  worst) resolution and make a corrective re-run inert. Last-writer-wins lets a
  re-run fix a bad early attach; per-run `entities_touched` (ADR 0010) preserves
  the audit trail.
- **Drop `run_cache`, rely solely on the durable lookup.** The `:RESOLVED_TO`
  edge is read-your-writes consistent, so the DB lookup *would* cover the
  within-run case — but the cache avoids a round-trip for bursts of variants and
  keeps the audited `attached_cache` accounting explicit. Kept as a fast path.
