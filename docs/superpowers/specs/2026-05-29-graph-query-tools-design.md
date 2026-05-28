# Graph-only query tools (round 1) — design

**Date:** 2026-05-29
**Status:** approved (design); pending implementation plan
**Scope:** add three pure-graph retrieval tools to the existing `posts_mentioning`
pattern, completing the *Network analysis* and *Aggregation* primary query types.

## Context & goal

The foundation dispatches one reference tool (`posts_mentioning`) end-to-end
through the structured UI with audit logging. The goal of this round is to
**cover the queries a non-technical analyst actually runs** against data that is
already in the graph — i.e. broaden the structured (point-and-click) surface,
which is also the prerequisite for a useful natural-language agent later (an
agent over a single tool has nothing to orchestrate).

Mapping chorus's four primary query types to cost:

| Primary query type | Tool | This round? |
|---|---|---|
| Enumeration | `posts_mentioning` | ✅ already shipped |
| Network analysis | `authors_connected_by_topic` | ✅ yes (graph-only) |
| Aggregation | `author_activity_summary`, `topic_co_occurrence` | ✅ yes (graph-only) |
| Semantic similarity | `semantic_search` | ❌ deferred — needs embedding backfill |

`semantic_search` is deferred because `Post.embedding` is never populated today
(see *Deferred*); the other starter tools traverse data that already exists.

## The load-bearing constraint: `MENTIONS` targets `:Alias`, not `:Entity`

The extraction stage (`chorus/ingestion/extraction.py::write_mentions`) writes:

```
(:Post)-[:MENTIONS {span_start, span_end, confidence, model_version}]->(:Alias {surface_form})
```

The resolution stage that would add `(:Alias)-[:RESOLVED_TO]->(:Entity)` is **not
yet landed**. So today the graph effectively contains **no `:Entity` nodes and no
`:RESOLVED_TO` edges** — mentions terminate at `:Alias`. `posts_mentioning.cypher`
already copes with this via `OPTIONAL MATCH (mention:Alias)-[:RESOLVED_TO]->(e:Entity)`
plus `COALESCE`.

**Rule for all three new tools:** a *topic* is the resolved `:Entity` when present,
else the `:Alias` surface form. The canonical join/identity key is:

```
topic_key = coalesce(e.id, al.surface_form)
```

**Consequence to disclose in every result:** today "shared topic" means *shared
exact alias surface form*. "Joe Biden" and "President Biden" will not cluster until
resolution lands. The tools run now over aliases and **improve automatically** when
resolution ships — no rewrite. Each tool's UI page must show a short note that
topic clustering is pending (consistent with chorus's standing rule to surface
known incompleteness rather than hide it — cf. the expected/collected engagement
delta and the quoting limitation).

## What carries over for free (no changes needed)

- **Dispatch:** `POST /tools/{name}` validates against the tool's input model and
  invokes `TOOLS[name].run` (the `@audited` wrapper). `GET /tools` emits JSON
  Schema per tool. Adding a tool = registering it; the router is generic.
- **UI client:** `ChorusClient.call_tool(name, payload)` is generic. Each tool
  needs only a new `ui/pages/NN_<tool>.py`.
- **Audit:** `@audited` writes exactly one row per call. Each output model
  implements `audit_entities()` (resolved entity ids surfaced; typically empty
  until resolution lands — that is correct and honest) and `audit_result_count()`.

## Tool specs

Each tool = one Cypher template in `queries/<name>.cypher` (never inline) + one
module in `tools/<name>.py` (`@register_tool` + `@audited` + Pydantic in/out) +
one Streamlit page in `ui/pages/`. Cypher skeletons below are illustrative, not
final; exact Cypher is written during implementation.

### 1. `author_activity_summary(author, from, to)` — aggregation (build first, simplest)

**Input**

- `author: str` — matches `:Author.handle` or `:Author.display_name`,
  case-insensitive (`handle` is indexed by `author_handle`).
- `from_: datetime | None` (alias `from`), `to: datetime | None` — bounds on
  `Post.timestamp`; `[from, to)` half-open, mirroring `posts_mentioning`.

**Behaviour**

A name may match multiple authors. Return **one summary per matched author** —
never silently merge distinct people. Each summary carries `author_id` / `handle`
/ `display_name` / `platform` so the analyst can disambiguate. A matched author
with no posts in the time window returns a zero-count summary (so "no activity in
range" is distinguishable from "no such author", which returns an empty list).

**Output** — `summaries: list[AuthorSummary]`, where `AuthorSummary`:

- `author_id, handle, display_name, platform`
- `post_count, posting_count, comment_count, message_count` (by specialization label)
- `first_activity: datetime | None`, `last_activity: datetime | None`
- engagement totals over the author's **Postings**:
  `expected_reactions_total, collected_reactions_total, expected_comments_total,
  collected_comments_total` — both sides stored so the UI surfaces the
  crawl-incompleteness delta (comment/message-specific counts e.g. `replies_count`
  are an explicit v1 omission).
- `top_topics: list[TopicCount]` (top 10 by mention count) where
  `TopicCount = {topic: str, entity_id: str | None, count: int}`.

**Traversal sketch**

```cypher
MATCH (a:Author)
WHERE toLower(coalesce(a.handle, "")) = toLower(trim($author))
   OR toLower(coalesce(a.display_name, "")) = toLower(trim($author))
OPTIONAL MATCH (a)-[:AUTHORED]->(p:Post)
  WHERE ($from IS NULL OR p.timestamp >= datetime($from))
    AND ($to   IS NULL OR p.timestamp <  datetime($to))
// aggregate counts / min/max ts / engagement sums per author a
// top_topics via a second OPTIONAL MATCH (p)-[:MENTIONS]->(al:Alias)
//   OPTIONAL MATCH (al)-[:RESOLVED_TO]->(e:Entity), grouped by topic_key
```

`audit_entities()` → distinct resolved `entity_id`s appearing in `top_topics`.

### 2. `topic_co_occurrence(topic, from, to, limit)` — aggregation

**Input**

- `topic: str` — alias surface form or entity canonical name, case-insensitive.
- `from_`, `to` — optional `Post.timestamp` bounds, half-open.
- `limit: int = 50` (1..500) — max co-occurring topics returned.

**Behaviour**

Find posts mentioning the seed topic, then the **other** topics mentioned in those
same posts, ranked by number of shared posts. **v1 is 1-hop** (same-post
co-occurrence). CLAUDE.md lists a `hops` parameter; multi-hop topic expansion is a
documented stretch (added traversal cost + fuzzier semantics), not in v1.

**Output**

- `seed: str` (matched seed name/key), `seed_entity_id: str | None`
- `cooccurring: list[CooccurringTopic]` where
  `CooccurringTopic = {topic: str, entity_id: str | None, count: int}` (count =
  shared post count), descending by `count`, capped at `limit`. The seed topic
  itself is excluded.

`audit_entities()` → seed `entity_id` (if resolved) + resolved `entity_id`s in
`cooccurring`.

### 3. `authors_connected_by_topic(seed_author, min_overlap, max_hops, limit)` — network analysis (build last, most complex)

**Input**

- `seed_author: str` — matched like `author_activity_summary`'s `author`.
- `min_overlap: int = 1` (>= 1) — minimum count of shared topic keys.
- `max_hops: int = 1` — **v1 supports 1 only** (direct topic neighbours). Values
  > 1 are rejected with `422` (documented stretch) rather than silently ignored.
- `limit: int = 50` (1..500) — max connected authors per seed.

**Behaviour**

For each matched seed author, find other authors who mention the same topic keys,
ranked by overlap (count of distinct shared topic keys) `>= min_overlap`. Exclude
the seed author. Consistent with tool 1, results are **grouped per matched seed
author** so multiple same-named seeds are surfaced, never merged.

**Output** — `results: list[SeedConnections]`, where:

- `SeedConnections = {seed: AuthorRef, connected: list[ConnectedAuthor]}`
- `AuthorRef = {author_id, handle, display_name}`
- `ConnectedAuthor = {author_id, handle, display_name, overlap: int, shared_topics: list[str]}`
  — `shared_topics` are topic-key display names; descending by `overlap`, capped at `limit`.

**Traversal sketch**

```cypher
MATCH (seed:Author) WHERE <name match on $seed_author>
MATCH (seed)-[:AUTHORED]->(:Post)-[:MENTIONS]->(al:Alias)
OPTIONAL MATCH (al)-[:RESOLVED_TO]->(e:Entity)
WITH seed, collect(DISTINCT coalesce(e.id, al.surface_form)) AS seed_topics
MATCH (other:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->(al2:Alias)
  WHERE other <> seed
OPTIONAL MATCH (al2)-[:RESOLVED_TO]->(e2:Entity)
WITH seed, other, seed_topics, coalesce(e2.id, al2.surface_form) AS k
  WHERE k IN seed_topics
WITH seed, other, collect(DISTINCT k) AS shared
  WHERE size(shared) >= $min_overlap
// group per seed, order by size(shared) desc, cap at $limit
```

`audit_entities()` → resolved `entity_id`s among shared topic keys across results.
(`Author.id` is already indexed/constrained, so the overlap join is index-backed —
see the connections-ingestion note in CLAUDE.md about MERGE-on-unindexed
degradation; these are reads but the same indexes apply.)

## Testing

Mirror the `tests/` layout (`tests/tools/test_<name>.py`). Run against an ephemeral
Neo4j (the scaffolding's chosen mechanism — test compose profile or testcontainers)
over a **small alias-based fixture** (aliases, not entities — matching today's
reality), plus a few resolved `Entity` nodes in at least one test to prove the
`COALESCE` path works both ways.

Per tool, assert:

- **Time-window correctness** — a dedicated regression test that a `[from, to)`
  bound is respected. This is the exact bug class that bit `posts_mentioning`
  (`AND`/`OR` precedence silencing the time filter); each tool gets one.
- Aggregation/overlap math (counts, `min_overlap` threshold, `limit` cap).
- Multi-match seed behaviour (per-author grouping, no merge).
- `max_hops > 1` → `422` for tool 3.
- `audit_entities()` / `audit_result_count()` populate the audit row.

Unit tests stub nothing graph-side (they need the graph); the inference provider is
not touched by these tools, so no inference stubbing is required.

## Build sequence

Independently shippable, simplest → hardest:

1. `author_activity_summary` (no topic-join subtlety)
2. `topic_co_occurrence` (introduces the `topic_key`/COALESCE join)
3. `authors_connected_by_topic` (per-seed grouping + overlap traversal)

Each step: Cypher template → tool module (register + audit + Pydantic) → UI page →
tests. No router/client edits.

## Resolved decisions (defaults baked in)

- **Author matching:** match on `handle`/`display_name` case-insensitively; return
  per-author results. Distinct same-named authors are never merged.
- **`hops`/`max_hops`:** ship 1-hop in v1; multi-hop is a documented stretch
  (tool 3 rejects `max_hops > 1` rather than silently degrading).
- **Topic identity:** `coalesce(e.id, al.surface_form)`; alias match is exact by
  stored surface form (case-folding deferred to the resolution stage).
- **Spec location:** `docs/superpowers/specs/` (brainstorming default), separate
  from the curated `docs/decisions/` ADRs.

## Deferred (with trigger)

- **`semantic_search`** — needs `Post.embedding` populated. Today nothing calls
  `provider.embed()` during ingestion (`orchestrator.py` does inline NER only); the
  `post_embedding` vector index exists (`003_vector_indexes.cypher`) but is empty.
  Trigger: its own round = batched embedding backfill over existing posts + wire
  embedding into the ingestion orchestrator + the vector-plus-graph query.
- **`network_around(entity, depth)`** — graph-only but its value is a network
  visualization, a different UI track than tabular result pages. Belongs with a
  viz-component story.
- **NL agent (power-user surface)** — explicitly out of this round. This work gives
  the agent a real tool menu (4 tools incl. `posts_mentioning`) to orchestrate when
  it is built.
- **Resolution stage** — not triggered by this work, but landing it strictly
  improves tools 2 and 3 (topic clustering) with no tool changes.
