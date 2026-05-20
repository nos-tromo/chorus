# 0002 — Deferred connections schema

Status: accepted
Date: 2026-05-20

## Context

CLAUDE.md anticipates four upstream artifact tables: `postings`,
`comments`, `messages`, and `connections` (follower/following +
friendship). The first three have stable column lists; the
`connections` table's columns are not yet pinned down.

Ingestion is not a hard blocker for v1 retrieval — the graph can be
populated with the first three artifacts and a useful set of queries
works without the social graph. But the Neo4j schema for the social
graph is cheap to add now and ruinously expensive to add later (bulk
MERGE on millions of edges without an index degrades catastrophically).

## Decision

Land the Neo4j schema for connections now, stub the ingestion path
explicitly. Specifically:

- Migration `001_constraints.cypher` includes
  `CREATE CONSTRAINT author_id … REQUIRE a.id IS UNIQUE`, so the
  eventual bulk load is index-backed.
- Migration `002_indexes.cypher` includes relationship indexes on
  `[:FOLLOWS]` and `[:FRIENDS_WITH]` (`crawled_at`).
- `chorus/ingestion/connections.py` raises `NotImplementedError` with
  the open questions listed in its docstring.
- The orchestrator catches that `NotImplementedError`, logs a
  `skipped` event, and continues. It does not silently drop rows — if
  the adapter ever returns connection rows, they are written to the
  raw store and the warning makes the gap visible.

## Open questions (carry-over from CLAUDE.md)

- Exact column names emitted by the upstream system.
- Edge properties — at minimum `crawled_at`; ideally `created_at`.
- Snapshot vs. delta semantics. Snapshots let chorus detect removals
  by diffing crawl runs; deltas without explicit removal events make
  "active network at time T" un-answerable.
- Canonical direction for `:FRIENDS_WITH` (stored once per pair).
- Handling of authors absent from artifact tables (create thin
  `:Author` nodes — multi-hop traversals require structural
  completeness).
- Dedupe strategy for bidirectional friendship rows (A→B and B→A).
- Self-loop filtering at ingestion.
- Batching strategy at load time — connections may dwarf artifact
  rows for organizations with rich social graphs.

## Alternatives considered

- **Add schema only when ingestion lands.** Cheapest now; catastrophic
  later if the first bulk load touches a non-indexed property.
- **Mock the schema with placeholder column names.** Risks committing
  to a wrong shape that has to be reverted; debugging incorrect
  ingestion is harder than building from a confirmed spec.

## Consequences

- Positive: when the schema arrives, ingestion is the only thing to
  write; the graph is already ready.
- Negative: `connections.py` carries a `NotImplementedError` that
  shows up in IDE warnings and linter scans.
- Reversal trigger: upstream publishes the connections schema —
  remove this ADR's "open questions" by filling in
  `chorus/ingestion/connections.py` and any required column-name
  config.
