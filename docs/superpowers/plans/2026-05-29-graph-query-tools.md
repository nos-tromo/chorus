# Graph-only query tools (round 1) — implementation plan

> **For agentic workers:** implement task-by-task with TDD (red → green → commit).
> Steps use checkbox (`- [ ]`) syntax. Integration tests need Docker running
> (testcontainers boots Neo4j 5.26.26).

**Status:** ✅ Implemented on `feat/graph-query-tools` (all three tasks committed;
full suite 91 passed; ruff + mypy clean).

**Goal:** Add three pure-graph retrieval tools — `author_activity_summary`,
`topic_co_occurrence`, `authors_connected_by_topic` — cloning the existing
`posts_mentioning` pattern, completing the *Network analysis* and *Aggregation*
primary query types over data already in the graph.

**Architecture:** Each tool = one Cypher template in `chorus/queries/` + one module
in `chorus/tools/` (`@register_tool` + `@audited` + Pydantic in/out) + one Streamlit
page in `chorus/ui/pages/`. No router or UI-client changes — `GET /tools`,
`POST /tools/{name}`, and `ChorusClient.call_tool` are already generic.

**Tech stack:** Python 3.12, FastAPI, Neo4j (Cypher), Pydantic, Streamlit, pytest +
testcontainers, ruff, mypy. Package manager: `uv`.

**Approved spec:** `docs/superpowers/specs/2026-05-29-graph-query-tools-design.md`.

---

## Context

The foundation dispatches one reference tool (`posts_mentioning`) end-to-end through
the structured UI with §76 audit logging. This round broadens the **structured
(point-and-click) analyst surface** — the explicitly-planned default surface for
non-technical users — and is the prerequisite for a useful NL agent later (an agent
over a single tool has nothing to orchestrate). It deliberately ships the three
graph-only starter tools and defers `semantic_search` (needs an embedding backfill —
`Post.embedding` is never populated today) and `network_around` (needs a viz UI).

## The load-bearing constraint (applies to every tool)

Extraction writes `(:Post)-[:MENTIONS]->(:Alias {surface_form})`. The resolution
stage that adds `(:Alias)-[:RESOLVED_TO]->(:Entity)` is **not landed**, so today the
graph effectively has **no `:Entity` nodes**. `posts_mentioning.cypher` already copes
via `OPTIONAL MATCH … RESOLVED_TO` + `CASE`. Every new tool follows the same rule:

- **topic display name** = `CASE WHEN m:Entity THEN m.canonical_name WHEN e IS NOT NULL THEN e.canonical_name ELSE m.surface_form END`
- **topic key** (identity/join) = `CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END`
- **entity_id** (audit/display) = `CASE WHEN m:Entity THEN m.id ELSE e.id END` (null for unresolved aliases)

Consequence surfaced in every UI page: today "shared topic" = shared exact alias
surface form; clustering ("Joe Biden" ≈ "President Biden") arrives with the resolution
stage, with **no tool changes**.

## Shared tool recipe (study `chorus/tools/posts_mentioning.py` first)

1. Pydantic input model (`from`/`to` use `Field(alias="from"/...)` + `model_config = {"populate_by_name": True}`).
2. Pydantic output model(s); the top-level output implements `audit_entities() -> list[str]` and `audit_result_count() -> int` (read by `@audited`).
3. Function signature: `def tool(driver, params, *, user, audit) -> OutModel:` with `del user, audit`.
4. Decorators, outer→inner: `@register_tool(...)` then `@audited`.
5. Load Cypher via `load_template("<name>")`; run in `with driver.session() as session:`.
6. Convert Neo4j temporals with a local `_native()` helper; cast the result to
   `datetime` so strict mypy (`warn_return_any`) is satisfied.

### Wiring each tool (two edits — the second prevents a real registry bug)

- **`chorus/tools/__init__.py`** — add the module to the self-registering import so it
  lands in `TOOLS`.
- **`tests/conftest.py`** — add the module path to `_CHORUS_ENV_MODULES`. That list is
  evicted from `sys.modules` before each test so the `TOOLS` registry is rebuilt
  consistently with `_audit` (which owns `TOOLS`). Omitting a tool module makes it go
  **missing from `TOOLS`** in any test running after another test imported it.

### Formatting gotcha (learned during execution)

`pre-commit run --all-files` only checks git-**tracked** files. A newly-written test
file is untracked, so ruff-format skips it during its own task and the next task's run
reformats it. **Stage new files (`git add`) before running pre-commit** so ruff sees
and normalizes them in the same task.

---

## Task 1 — `author_activity_summary` (aggregation; simplest, build first)

**Files**: `chorus/queries/author_activity_summary.cypher`, `chorus/tools/author_activity_summary.py`,
modify `chorus/tools/__init__.py` + `tests/conftest.py`,
`tests/integration/test_author_activity_summary.py`, `chorus/ui/pages/02_author_activity_summary.py`.

TDD: write failing tests (empty, aggregate counts/topics, time-window regression,
same-name-not-merged, registered) → confirm red → write Cypher (two `CALL { WITH a … }`
subqueries each ending in an aggregation/`collect` so the author row survives a
zero-post author) → write the tool module (input `AuthorActivitySummaryIn`; nested
`TopicCount` / `AuthorSummary`; `AuthorActivitySummaryOut` with `audit_result_count =
len(summaries)` and `audit_entities` = distinct non-null `entity_id` in `top_topics`,
top 10) → wire registration → green → UI page (one block per summary; pending-resolution
caption) → verify + commit `feat(tools): add author_activity_summary graph query tool`.

Cypher (final):

```cypher
MATCH (a:Author)
WHERE toLower(coalesce(a.handle, "")) = toLower(trim($author))
   OR toLower(coalesce(a.display_name, "")) = toLower(trim($author))
CALL {
  WITH a
  OPTIONAL MATCH (a)-[:AUTHORED]->(p:Post)
    WHERE ($from IS NULL OR p.timestamp >= datetime($from))
      AND ($to   IS NULL OR p.timestamp <  datetime($to))
  RETURN
    count(p)                                AS post_count,
    count(CASE WHEN p:Posting THEN 1 END)   AS posting_count,
    count(CASE WHEN p:Comment THEN 1 END)   AS comment_count,
    count(CASE WHEN p:Message THEN 1 END)   AS message_count,
    min(p.timestamp)                        AS first_activity,
    max(p.timestamp)                        AS last_activity,
    sum(coalesce(p.expected_reactions, 0))  AS expected_reactions_total,
    sum(coalesce(p.collected_reactions, 0)) AS collected_reactions_total,
    sum(coalesce(p.expected_comments, 0))   AS expected_comments_total,
    sum(coalesce(p.collected_comments, 0))  AS collected_comments_total
}
CALL {
  WITH a
  OPTIONAL MATCH (a)-[:AUTHORED]->(p2:Post)
    WHERE ($from IS NULL OR p2.timestamp >= datetime($from))
      AND ($to   IS NULL OR p2.timestamp <  datetime($to))
  OPTIONAL MATCH (p2)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH p2,
    CASE WHEN m:Entity THEN m.canonical_name
         WHEN e IS NOT NULL THEN e.canonical_name
         ELSE m.surface_form END AS topic,
    CASE WHEN m:Entity THEN m.id ELSE e.id END AS entity_id
  WHERE topic IS NOT NULL
  WITH topic, entity_id, count(DISTINCT p2) AS cnt
  ORDER BY cnt DESC, topic ASC
  LIMIT 10
  RETURN collect({topic: topic, entity_id: entity_id, count: cnt}) AS top_topics
}
RETURN
  a.id AS author_id, a.handle AS handle, a.display_name AS display_name, a.platform AS platform,
  post_count, posting_count, comment_count, message_count,
  first_activity, last_activity,
  expected_reactions_total, collected_reactions_total,
  expected_comments_total, collected_comments_total,
  top_topics
ORDER BY author_id;
```

---

## Task 2 — `topic_co_occurrence` (aggregation)

**Files**: `chorus/queries/topic_co_occurrence.cypher`, `chorus/tools/topic_co_occurrence.py`,
modify `chorus/tools/__init__.py` + `tests/conftest.py`,
`tests/integration/test_topic_co_occurrence.py`, `chorus/ui/pages/03_topic_co_occurrence.py`.

> Spec trim (flagged): `seed_entity_id` dropped for v1 — always null pre-resolution and
> recovering it adds query complexity for no present value. Output is `seed` (echo) +
> `cooccurring`.

TDD: failing tests (empty, ranked + seed-excluded, time-window regression, registered)
→ red → Cypher (collect each post's topics, keep posts whose topics include the seed by
name, unwind and `count(DISTINCT p)` per other topic) → tool module (`TopicCoOccurrenceIn`
with `limit` in [1,500]; `CooccurringTopic`; `TopicCoOccurrenceOut(seed, cooccurring)`)
→ wire → green → UI page → commit `feat(tools): add topic_co_occurrence graph query tool`.

Cypher (final):

```cypher
MATCH (p:Post)-[:MENTIONS]->(m)
  WHERE ($from IS NULL OR p.timestamp >= datetime($from))
    AND ($to   IS NULL OR p.timestamp <  datetime($to))
OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH p, toLower(trim($topic)) AS q,
  CASE WHEN m:Entity THEN m.canonical_name
       WHEN e IS NOT NULL THEN e.canonical_name
       ELSE m.surface_form END AS topic,
  CASE WHEN m:Entity THEN m.id ELSE e.id END AS entity_id
WITH p, q, collect({topic: topic, entity_id: entity_id}) AS topics
WITH p, q, topics, [t IN topics WHERE toLower(coalesce(t.topic, "")) = q] AS seed_hits
WHERE size(seed_hits) > 0
UNWIND topics AS t
WITH q, p, t
WHERE toLower(coalesce(t.topic, "")) <> q
WITH t.topic AS topic, t.entity_id AS entity_id, count(DISTINCT p) AS count
ORDER BY count DESC, topic ASC
LIMIT $limit
RETURN topic, entity_id, count;
```

---

## Task 3 — `authors_connected_by_topic` (network analysis; most complex, build last)

**Files**: `chorus/queries/authors_connected_by_topic.cypher`, `chorus/tools/authors_connected_by_topic.py`,
modify `chorus/tools/__init__.py` + `tests/conftest.py`,
`tests/integration/test_authors_connected_by_topic.py`, `chorus/ui/pages/04_authors_connected_by_topic.py`.

> `audit_entities()` returns `[]` for v1 (shared topics are alias surface forms with no
> entity id pre-resolution). `audit_result_count()` = total connected authors across seeds.

TDD: failing tests (max_hops>1 → ValidationError, empty-seed one-group-no-connections,
connected-by-overlap, registered) → red → Cypher (first subquery collects the seed's
topic keys; second matches other authors, keeps shared keys, `count(DISTINCT key)` as
overlap ≥ `$min_overlap`, ordered/limited, collected per seed) → tool module
(`AuthorsConnectedByTopicIn` with `field_validator("max_hops")` rejecting `> 1`;
`AuthorRef` / `ConnectedAuthor` / `SeedConnections`; `AuthorsConnectedByTopicOut(results)`)
→ wire → green → UI page (per-seed groups) → commit
`feat(tools): add authors_connected_by_topic graph query tool`.

Cypher (final):

```cypher
MATCH (seed:Author)
WHERE toLower(coalesce(seed.handle, "")) = toLower(trim($seed_author))
   OR toLower(coalesce(seed.display_name, "")) = toLower(trim($seed_author))
CALL {
  WITH seed
  OPTIONAL MATCH (seed)-[:AUTHORED]->(:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END AS key
  WHERE key IS NOT NULL
  RETURN collect(DISTINCT key) AS seed_keys
}
CALL {
  WITH seed, seed_keys
  MATCH (other:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->(m2)
    WHERE other <> seed
  OPTIONAL MATCH (m2:Alias)-[:RESOLVED_TO]->(e2:Entity)
  WITH seed_keys, other,
    CASE WHEN m2:Entity THEN m2.id ELSE coalesce(e2.id, m2.surface_form) END AS key2,
    CASE WHEN m2:Entity THEN m2.canonical_name
         WHEN e2 IS NOT NULL THEN e2.canonical_name
         ELSE m2.surface_form END AS name2
  WHERE key2 IN seed_keys
  WITH other, collect(DISTINCT name2) AS shared_topics, count(DISTINCT key2) AS overlap
  WHERE overlap >= $min_overlap
  ORDER BY overlap DESC, other.id ASC
  LIMIT $limit
  RETURN collect({
    author_id: other.id, handle: other.handle, display_name: other.display_name,
    overlap: overlap, shared_topics: shared_topics
  }) AS connected
}
RETURN seed.id AS seed_author_id, seed.handle AS seed_handle,
       seed.display_name AS seed_display_name, connected
ORDER BY seed_author_id;
```

---

## Final verification (all passed)

- `uv run pytest` — full suite **91 passed**.
- `uv run pre-commit run --all-files` — ruff (lint+format) + mypy clean.
- `GET /tools` now exposes four tools; `max_hops > 1` is rejected at input validation
  (HTTP 422 via the router's existing `ValidationError` handling).
- Manual UI smoke (optional, needs Neo4j + API up): pages 02–04 under the Streamlit
  sidebar.

## Notes

- Integration tests use the `migrated_driver` + `in_memory_audit` fixtures and a small
  **alias-based** fixture graph (matching today's pre-resolution reality), with at least
  one resolved `:Entity` exercised via the `COALESCE` path.
- No changes to `chorus/api/routers/tools.py` or `chorus/ui/client.py` — dispatch and the
  UI client are generic.
- Landing the resolution stage later strictly improves tools 2 and 3 (topic clustering)
  with **no tool changes**.
