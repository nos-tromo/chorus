# 0003 — Vectors live in Neo4j

Status: accepted
Date: 2026-05-20

## Context

GraphRAG over social posts needs both semantic search (vector
similarity over post text) and graph traversal (authors connected by
shared topics, posts mentioning an entity). The two are mixed in many
queries: "find posts semantically similar to X, then return the
authors connected to them by mutual entity mentions."

## Decision

Store embeddings as properties on `:Post` and `:Entity` nodes,
indexed via Neo4j's native vector indexes (5.11+). Vector lookup and
graph traversal happen in the same Cypher statement; there is no
separate vector store.

## Alternatives considered

- **Qdrant as a separate vector store.** Mature, fast at scale, used
  by docint. But every "find similar posts then expand to authors"
  query becomes two round-trips with manual ID joining, and the join
  set sizes (top-k similarity expansions) hit Qdrant's per-request
  limits at moderate `k`. Operating two stateful services also doubles
  the backup, retention, and compliance surface.
- **pgvector in a separate Postgres.** Same two-store problem as
  Qdrant; loses Cypher's expressive graph traversal which is the
  primary reason we're on Neo4j in the first place.
- **In-process FAISS.** Doesn't scale beyond a single process; loses
  durability and incremental updates.

## Consequences

- Positive: one stateful service, one backup story, one Cypher
  expression spans vector + graph, lower operational complexity.
- Negative: Neo4j's vector index is newer than purpose-built vector
  stores; performance characteristics at very large scale (>100M
  vectors) are less well-known. Mitigated by the fact that chorus's
  upstream is bounded — a single organizational source, not the open
  internet.
- Reversal trigger: if vector recall at our scale becomes a bottleneck
  Neo4j cannot keep up with, swap in Qdrant via a thin facade in
  `chorus/inference/vectors.py` (does not exist yet) and keep the
  Cypher patterns as fallbacks. Re-embedding all posts is a one-time
  cost we plan for.
