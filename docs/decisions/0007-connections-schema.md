# 0007 — Connections schema (locked)

Status: accepted
Date: 2026-05-27
Supersedes: [0002](0002-deferred-connections-schema.md)

## Context

ADR 0002 landed the Neo4j schema for the social graph (`:Author(id)`
unique constraint, relationship indexes on `:FOLLOWS` and
`:FRIENDS_WITH` keyed on `crawled_at`) but deferred the ingestion
implementation because the upstream `connections` table's column shape
was unknown.

A production sample (387 rows, single Instagram crawl) now answers
every open question ADR 0002 listed. Notably the data shape diverges
from what ADR 0002 sketched:

- Each row describes **one connected user with respect to a constant
  target user** (the "selected conn. User" columns). A single
  `connections.csv` file may carry rows for multiple targets
  concatenated; each row independently identifies `(row_user_id,
  target_id)`.
- Relationships are encoded as three Yes/No flag columns — `Friend`,
  `Follower`, `Following` — which can coexist on one row (mutual
  follow → `Follower=Yes` and `Following=Yes`).
- Per-pair engagement metrics (`Posting Conn.`, `Comment Conn.`,
  `Reaction Conn.`, `React. Like|Love|Haha|Wow|Sad|Angry`,
  `ChatMessage Conn.`, `Media Conn.`) are present but all zero in the
  Instagram sample. They may carry signal on other platforms.
- The row also carries denormalized profile data for the row user
  (Name, Vanity Name, Bio, Hometown, …) overlapping `profiles.csv`.
  Per ADR 0006, `profiles.csv` is the authoritative source for
  `:Author` identity.

## Decision

### Row → graph projection

For each row that passes `from_row`:

| Flag | Edge |
| --- | --- |
| `Follower=Yes` | `(row_user)-[:FOLLOWS {crawled_at}]->(target)` |
| `Following=Yes` | `(target)-[:FOLLOWS {crawled_at}]->(row_user)` |
| `Friend=Yes` | `(a)-[:FRIENDS_WITH {crawled_at}]->(b)` where `a.id < b.id` lexicographically |

Flags coexist: a single row can emit zero, one, two, or three edges.
`counts["connections"]` returned by the orchestrator reports edges
written, not rows ingested.

### Canonical direction for `:FRIENDS_WITH`

Picked at DTO assembly: `sorted((row_user_id, target_id))` →
`(from_id, to_id)`. Re-emission of the same pair from either
orientation MERGEs to the same edge. Queries treat it as undirected
(`MATCH (a)-[:FRIENDS_WITH]-(b)`) per CLAUDE.md §Data model.

### `:Author` identity from connections.csv

Endpoints are MERGEd with `ON CREATE SET` only — never `SET`. The
identity columns connections carries (handle, display name, URL,
platform, profile type) write only when the `:Author` node is first
created. `profiles.csv` remains authoritative per ADR 0006 and is not
overwritten by a subsequent connections ingest.

The denormalized aggregate columns on the row user (`Postings`,
`Comments`, `Friends`, `Connections` counts, …) are **not** mapped to
the graph — they are derivable from the artifact tables and the social
graph itself once ingested.

### Engagement metrics

Deliberately not mapped in v1. The raw store retains every column
verbatim, and the metrics are derivable from postings, comments, and
reaction edges once those land in the graph. Adding edge properties
later is a non-breaking migration (no relationship data needs
re-ingest).

### Filtering at `from_row`

- **Self-loops** (`row_user_id == target_id`): dropped with WARNING.
  Trivial to construct (target accidentally referenced as itself) and
  always meaningless.
- **No-signal rows** (all three flags `No`): dropped with INFO. The row
  has no graph projection; the raw store still retains it.

### Re-crawl semantics

Snapshot-additive. `MERGE` is idempotent on edges; `crawled_at` uses
`SET` (latest wins) so every encounter advances the freshness stamp.
Removed relationships are **not** detected — once an edge lands, it
stays until a future delta-aware sweep is built. This is a known
limitation, documented here and in `docs/retention.md`.

### Batching

UNWIND per call to `connections.write_batch`, with the orchestrator
flushing in chunks of `_CONNECTIONS_BATCH_SIZE = 500` DTOs. Three
phases per batch (one Neo4j session):

1. Endpoint `:Author` upsert (one combined list, deduped client-side).
2. `:FOLLOWS` UNWIND (both directions resolved client-side, mixed
   together).
3. `:FRIENDS_WITH` UNWIND (canonical direction already applied).

Index-backed via migrations 001 + 002.

### Adapter contract

The upstream emits one `connections.csv` per delivery; the file may
contain rows for one or many targets concatenated. The adapter does
not partition by target; each row independently identifies its
`(row_user, target)` pair. If upstream ever emits per-target files,
they are pre-merged into one file before chorus ingestion.

## Alternatives considered

- **Engagement metrics as `:FOLLOWS` edge properties.** Rejected for
  v1: clean asymmetry doesn't fit the symmetric engagement counts;
  rows with only `Friend=Yes` have no `:FOLLOWS` edge to hang the
  counts on; and the metrics are derivable from the graph once
  reactions are ingested. Re-evaluate when a query needs ranking.
- **Snapshot with stale-edge sweep.** Rejected for v1: requires
  careful tagging of edges with a run id and complicates partial
  crawls. Defer until a real use case needs removal detection.
- **Distinct row per relationship type.** Rejected — the upstream
  emits all three flags on one row; modeling three separate rows
  internally would force the writer to dedupe what the reader could
  trivially observe.
- **Treat `connections.csv` as authoritative for `:Author` identity.**
  Rejected — ADR 0006 fixes profiles as authoritative. Connections is
  edge data; identity overlap is denormalization, not authority.

## Consequences

- Positive: social-graph queries (`authors_connected_by_topic`,
  `network_around`, friend-of-friend traversals) are now structurally
  unblocked. Ingestion is index-backed from the first row.
- Positive: the three-flag dispatch lets a single row carry full
  relationship state (mutual follow, friend-and-follower, etc.) without
  reader-side reconciliation.
- Negative: v1 cannot detect "no longer following" / "no longer
  friends." Operators viewing the graph at time T see all
  relationships ever crawled, not only those still active at T.
- Negative: engagement-weighted traversal queries require a future
  migration to add edge properties. The raw store has the data; the
  graph does not.
- Negative: rows with denormalized profile data that contradicts
  `profiles.csv` are silently ignored (by design — `ON CREATE SET`),
  which can mask upstream data-quality issues. Operators should
  compare profile vs. connection identity if reconciliation is needed.
- Reversal trigger: production query patterns demand engagement
  ranking, removal detection, or per-target file partitioning. Each
  triggers its own follow-up ADR; none requires reversing this one.
