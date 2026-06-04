# `network_around` tool — design

**Date:** 2026-06-04
**Status:** proposed (design); pending approval → implementation plan
**Scope:** add one graph-only retrieval tool, `network_around(entity, depth,
limit, topic_limit)`, that returns the **ego network** around a topic as a
node/edge list for visualization. It is the visual companion to the tabular
enumeration/aggregation tools and the last starter tool listed in CLAUDE.md's
retrieval set (`network_around(entity, depth)` — "for visualization").

## Context & goal

Chorus ships five graph tools (`posts_mentioning`, `authors_mentioning`,
`author_activity_summary`, `topic_co_occurrence`, `authors_connected_by_topic`)
plus an NL agent. Every one returns a **table**. CLAUDE.md's *Network analysis*
query type and the original graph-tools round
(`2026-05-29-graph-query-tools-design.md`) both named `network_around` but
**deferred it on purpose**, with this trigger:

> `network_around(entity, depth)` — graph-only but its value is a network
> visualization, a different UI track than tabular result pages. Belongs with
> a viz-component story.

This is that story. The traversal it needs already lives in the existing tools
(the `(:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->(:Alias)→(:Entity)` leg powers
`authors_mentioning`, `topic_co_occurrence`, and `authors_connected_by_topic`);
what is new is (a) returning a **graph** rather than a ranked list and (b) a UI
page that **draws** it. The query stays in version-controlled Cypher; the agent
still never writes Cypher.

**Goal:** expose the entity's surrounding author/topic network as a dedicated,
audited, single-purpose tool whose output is directly renderable, and add the
first visualization page to the structured UI — without breaking the airgap
constraint and without a heavyweight new dependency.

## What the network *is* (the load-bearing design choice)

A raw "expand all edges to `depth` hops from the seed node" is rejected for v1:
it returns a multi-label soup (`Post`, `Alias`, `Entity`, `Author`, `Hashtag`,
`Platform`, `Group`, …) that is unbounded at depth 2 and illegible to draw. The
analytically meaningful network for social-network analysis is the **bipartite
Author ↔ Topic ego graph** centered on the seed topic — the same projection the
sibling tools already compute, just returned as nodes + edges instead of a
ranked table:

- **Nodes** are *topics* and *authors*.
  - A **topic** node is the resolved `:Entity` when present, else the `:Alias`
    surface form — the standard `coalesce(entity, alias)` identity used across
    every graph tool. So clustering improves automatically when resolution runs,
    no tool change (same guarantee as the round-1 tools).
  - An **author** node is an `:Author`.
- **Edges** are *Author —mentions→ Topic*, one per (author, topic) pair, with
  `weight` = count of **distinct posts** by that author mentioning that topic
  (mirrors `authors_mentioning`'s `mention_post_count` counting unit). No
  author–author or topic–topic edges in v1 (see *Deferred*).

The network is built in concentric rings from the seed topic **S**:

| `depth` | rings included | shape |
|---|---|---|
| 1 | S + authors who mention S (edges A–S) | a **star**: who talks about X |
| 2 | the above + the **other topics** those authors mention (edges A–T₂) | **topic → authors → co-topics**: the co-occurrence neighbourhood with the authors that connect it |

Odd depth ends on an author ring, even depth ends on a topic ring. v1 supports
`depth ∈ {1, 2}`; `depth > 2` is rejected at input validation (HTTP 422),
mirroring `authors_connected_by_topic`'s `max_hops` rule ("ship the low hop count
in v1; deeper traversal is a documented stretch, rejected rather than silently
degraded"). Depth 2 is the differentiating value — depth 1 is `authors_mentioning`
drawn as a star; depth 2 is the network a tabular tool *cannot* convey.

Because authors in ring A₁ also mention S (S is one of their topics), the depth-2
edge set is a strict superset of the depth-1 edge set — the seed star is always
present, with co-topics added around it.

## Matching semantics — mirror `posts_mentioning` exactly (load-bearing)

The seed `entity` string is matched against the `MENTIONS` target with the **same
parenthesized rule** `posts_mentioning.cypher` / `authors_mentioning.cypher` use.
For a trimmed, case-folded query `q`, a mention node `m` matches when either:

- `m` is an `:Entity` and `toLower(m.canonical_name) = q`, **or**
- `m` is an `:Alias` and (`toLower(m.surface_form) = q` **or** its resolved
  entity's `toLower(e.canonical_name) = q`, via `(m)-[:RESOLVED_TO]->(e)`).

This gives `network_around(X)` at depth 1 the **same author set** as
`authors_mentioning(X)` (for text-bearing posts — the universe mentions attach
to), making the tool trivially explainable and cross-tool testable. Reuse the
exact `AND`/`OR` parenthesisation — the unparenthesised-`OR` precedence bug is the
class the time-window regression test guards against, even though v1 has no time
filter (see *Deferred*).

## Bounding (a viz must be bounded, and so must the query)

Two deterministic caps keep both the picture legible and the read cheap:

- `limit: int = 25` (1..200) — max authors in the **A₁ ring**, the primary
  fan-out, kept by descending mention-count of the seed (tiebreak `author_id`),
  exactly like `authors_mentioning`'s ranking.
- `topic_limit: int = 50` (1..500) — max distinct **second-ring topics** (T₂),
  kept by descending total edge weight across the retained authors (tiebreak
  topic display name). Ignored at depth 1.

Both caps are applied in Cypher (auditable, deterministic). The output carries a
`truncated: bool` set true when either cap dropped nodes, so the UI can disclose
that the drawn network is a capped view — consistent with chorus's standing rule
to surface incompleteness rather than hide it.

## Tool spec

One Cypher template in `queries/network_around.cypher` (never inline) + one module
in `tools/network_around.py` (`@register_tool` + `@audited` + Pydantic in/out) +
one Streamlit page `ui/pages/06_network_around.py`. The Cypher below is
illustrative; exact Cypher is written during implementation.

### Input — `NetworkAroundIn`

- `entity: str` — entity canonical name or alias surface form, case-insensitive
  (matched per *Matching semantics*).
- `depth: int = 1` (1..2) — ring count. `depth > 2` rejected via
  `field_validator` (surfaced as 422), mirroring `max_hops`. Default 1 (the cheap
  default, matching the codebase's conservative style); the UI defaults the slider
  to 2 to show the differentiating view.
- `limit: int = 25` (1..200) — max authors in the A₁ ring.
- `topic_limit: int = 50` (1..500) — max second-ring topics (depth 2 only).

No `from`/`to` in v1 (see *Deferred*); when added they follow the half-open
`[from, to)` convention of the other tools.

### Output — `NetworkAroundOut`

A renderer-ready graph. Node ids are **namespaced and globally unique** so any
graph renderer (DOT, agraph, d3) can consume them without re-disambiguating an
author id that happens to equal a topic surface form:

- `seed: str` — matched seed display name/key (echo).
- `seed_node_id: str | None` — the seed topic's node id, or `None` when the seed
  matched nothing (empty network).
- `nodes: list[NetworkNode]`, `NetworkNode`:
  - `id: str` — `"topic:<topic_key>"` or `"author:<author_id>"`.
  - `kind: Literal["topic", "author"]`.
  - `label: str` — topic display name (entity canonical name or surface form) or
    author handle/display name.
  - `entity_id: str | None` — resolved `:Entity.id` for topic nodes (null for
    unresolved aliases and for author nodes).
  - `is_seed: bool` — true for the seed topic node only.
- `edges: list[NetworkEdge]`, `NetworkEdge`:
  - `source: str`, `target: str` — node ids (`source` = author, `target` = topic).
  - `weight: int` — distinct mentioning-post count for that (author, topic) pair.
- `truncated: bool` — true when `limit`/`topic_limit` dropped nodes.

Audit hooks:

- `audit_entities()` → distinct non-null `entity_id`s across all topic nodes
  (seed + rings), deduped in first-seen order. Empty when nothing resolved —
  correct and honest, exactly like the sibling tools.
- `audit_result_count()` → `len(nodes)` (the size of the network returned).

### Traversal sketch

```cypher
// Ring A1: authors who mention the seed, ranked, capped at $limit.
MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(m)
OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH a, p, m, e, labels(m) AS ml, toLower(trim($entity)) AS q
WHERE (
        ("Entity" IN ml AND toLower(coalesce(m.canonical_name,"")) = q)
     OR ("Alias"  IN ml AND (toLower(coalesce(m.surface_form,"")) = q
                          OR toLower(coalesce(e.canonical_name,"")) = q))
      )
WITH a,
     count(DISTINCT p) AS w_seed,
     // stable seed topic identity/label for the seed node
     head(collect(CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END)) AS seed_key,
     head(collect(CASE WHEN m:Entity THEN m.canonical_name
                       WHEN e IS NOT NULL THEN e.canonical_name
                       ELSE m.surface_form END)) AS seed_label,
     head(collect(CASE WHEN m:Entity THEN m.id ELSE e.id END)) AS seed_entity_id
ORDER BY w_seed DESC, a.id ASC
LIMIT $limit
WITH collect({author: a, w_seed: w_seed}) AS ring1, seed_key, seed_label, seed_entity_id
// Ring T2 (only when $depth >= 2): other topics the ring-1 authors mention,
// edge weight = count(DISTINCT post), capped to $topic_limit by total weight.
// Emit seed star edges always; co-topic edges when $depth >= 2.
// Final RETURN assembles nodes[] (seed topic + authors + ring-2 topics) and
// edges[] (author→topic, namespaced ids), plus truncated flags.
```

Exact ring-2 expansion + the `truncated` computation are finalized in
implementation; the shape above pins the contract.

## Visualization — the new UI track (why this was deferred, and the airgap call)

This is chorus's first **drawn** result page. The renderer must work in the
airgapped production environment — **no runtime network calls, no CDN-loaded JS**
(CLAUDE.md hard rule; cf. `docs/airgap.md`).

**v1 chooses `st.graphviz_chart` with a DOT string — zero new dependencies.**
`st.graphviz_chart` accepts a DOT string and renders it **client-side** with the
viz.js bundle Streamlit already ships in its static assets; it needs neither the
`graphviz` Python package nor the system `graphviz` binary, and makes no outbound
request. The page builds DOT from the tool's `nodes`/`edges` (seed node
highlighted, authors vs topics styled distinctly, edge `weight` → pen width). This
keeps the airgap clean and adds nothing to `pyproject.toml`.

Rejected for v1:

- **`pyvis`** — generates HTML that loads vis.js from a **CDN by default**; an
  airgap liability that needs asset-localisation work. Not worth it for v1.
- **`streamlit-agraph`** (interactive, click/drag) — nicer UX but a third-party
  Streamlit component: adds a dependency whose offline asset-serving and
  transitive deps must clear the *Dependency review* checklist in CLAUDE.md /
  `docs/airgap.md`. Deferred to a follow-up with that review as the explicit
  trigger.

The DOT path proves the end-to-end tool-to-picture loop now; swapping in an
interactive component later is a UI-only change — the tool's node/edge contract is
already renderer-agnostic.

> **Implementation must verify** (airgap gate): `st.graphviz_chart(dot_string)`
> renders with no system graphviz binary installed and issues no network request.
> If that proves false, fall back to emitting the same DOT for download +
> `streamlit-agraph` behind a dependency review — do not reach for a CDN renderer.

## What carries over for free (no changes needed)

- **Dispatch is registry-driven.** `api/routers/tools.py` builds `GET /tools` /
  `POST /tools/{name}` by iterating `TOOLS`; the agent builds its tool list from
  the same registry (`agent/openai_tools.py`). Registering surfaces the tool to
  both the REST surface and the agent — no router, client, or agent edits.
- **UI client is generic.** `ChorusClient.call_tool(name, payload)` already
  returns the JSON output; the new page only needs DOT-building + `graphviz_chart`.
- **Audit.** `@audited` writes exactly one row per call from `audit_entities()` +
  `audit_result_count()`.

### Agent exposure (a real consideration, resolved for v1)

Registration auto-exposes the tool to the NL agent, and a node/edge dump is poor
to narrate and can bloat the agent's context. v1 keeps it exposed (zero infra
change) but writes a first-docstring-line description that steers the model to use
it only when a *network/graph* is asked for, and to report **summary** facts
(network size, central authors) rather than echo the raw graph:

> `Return the author↔topic network around an entity (a node/edge graph for
> visualization), out to a small depth. Prefer the tabular tools for ranked
> answers; use this when the user asks to see the surrounding network.`

If the agent proves noisy with it, the clean follow-up is an `agent_exposed: bool`
flag on `ToolSpec` (filter in `openai_tools.tool_definitions()`) — out of scope
here, noted in *Deferred*.

## Testing

New file `tests/integration/test_network_around.py`, against an ephemeral Neo4j
over a small alias-based fixture (plus one resolved `:Entity` to exercise the
`coalesce` path both ways). Assert:

- **Depth-1 star** — `nodes` = seed topic + the authors mentioning it; every edge
  is author→seed with `weight` = distinct mentioning-post count; no second-ring
  topics present.
- **Depth-1 / `authors_mentioning` lockstep** — over a timestamped fixture with no
  cap pressure, the author node ids from `network_around(X, depth=1)` equal the
  authors from `authors_mentioning(X)`. Pins the mirror guarantee.
- **Depth-2 expansion** — second-ring co-topics appear with author→topic edges;
  the seed star edges are still present (superset property).
- **Resolved + unresolved both match** — seeding by canonical name and by surface
  form both build the network; topic node `entity_id` is set for the resolved
  topic and null for the alias-only one; `audit_entities()` carries the resolved
  id and is empty for an alias-only seed.
- **Bounding** — `limit` caps the author ring and `topic_limit` caps the topic
  ring deterministically (by weight); `truncated` flips true when a cap bites and
  false otherwise.
- **`depth > 2` → `ValidationError`** (→ 422), mirroring `max_hops`.
- **No-merge** — two distinct authors with the same display name are two nodes.
- **Empty seed** — a seed matching nothing returns empty `nodes`/`edges`,
  `seed_node_id = None`, `truncated = False`.
- **Audit** — `audit_result_count()` (= node count) and `audit_entities()`
  populate the row.

Pure graph read — the inference provider is untouched, so no inference stubbing.

A small **UI unit test** for the DOT builder (pure function: nodes/edges → DOT
string) asserting the seed node is highlighted and edges are emitted — no
Streamlit runtime needed.

## Build sequence (TDD)

1. `tests/integration/test_network_around.py` — red first.
2. `chorus/queries/network_around.cypher`.
3. `chorus/tools/network_around.py` (`@register_tool` + `@audited` + Pydantic).
4. `chorus/tools/__init__.py` — add the import line (self-registers → REST +
   agent) **and** add the module path to `tests/conftest.py::_CHORUS_ENV_MODULES`
   (omitting it makes the tool vanish from `TOOLS` in later tests — documented
   gotcha from the round-1 plan).
5. `chorus/ui/pages/06_network_around.py` — DOT builder (unit-tested) +
   `st.graphviz_chart`; slider defaults `depth=2`; caption discloses the
   capped-view / truncation note. Verify the airgap render gate above.

No router/client/agent edits.

## Resolved decisions (defaults baked in)

- **Network shape:** bipartite Author↔Topic ego graph (not a raw all-edge
  neighbourhood). Topic identity = `coalesce(entity, alias)`. Edge weight =
  distinct mentioning-post count.
- **Matching:** mirror `posts_mentioning` / `authors_mentioning` verbatim
  (depth-1 lockstep with `authors_mentioning`). Not entity-spanning.
- **Depth:** v1 supports `{1, 2}`, default 1, reject `> 2` at validation (the
  `max_hops` precedent). UI defaults the slider to 2.
- **Bounding:** `limit` (authors, by seed mention-count) + `topic_limit`
  (co-topics, by weight), both in Cypher, deterministic; `truncated` disclosed.
- **Renderer:** `st.graphviz_chart` + DOT string — zero new dependencies,
  airgap-safe. Interactive component deferred behind dependency review.
- **Output:** renderer-agnostic node/edge list with namespaced node ids.
- **No ADR warranted** — this adds a tool within the existing, ADR-backed
  retrieval pattern and reverses no load-bearing choice. The renderer choice
  *avoids* triggering dependency review precisely by adding no dependency; picking
  an interactive component later is what would trigger one.
- **Spec location:** `docs/superpowers/specs/` (brainstorming default), separate
  from the curated `docs/decisions/` ADRs.

## Deferred (with trigger)

- **Interactive viz component** (`streamlit-agraph` or similar: pan/zoom, click a
  node to re-seed). Trigger: analysts asking to explore the network interactively.
  Requires a dependency review (`docs/airgap.md`) for offline asset serving. The
  tool's node/edge contract already supports it — UI-only change.
- **`depth > 2` / variable-depth BFS.** Trigger: a concrete multi-hop network
  ask. Adds traversal cost and fuzzier semantics; same posture as the round-1
  `max_hops`/`hops` stretch.
- **Time-window filter (`from`/`to`).** Trigger: "show the network around X *in
  Q1*". Cheap additive change following the half-open `[from, to)` convention; the
  parenthesised match clause is already in place to bolt it onto safely.
- **Author–author edges** (co-authorship / `FOLLOWS` / `FRIENDS_WITH`) and
  **topic–topic** co-occurrence edges layered onto the same picture. Trigger: a
  request to see the *social* graph, not just the topic-bipartite projection.
  Additive node/edge kinds; the output model already carries a `kind` field.
- **`agent_exposed` flag on `ToolSpec`.** Trigger: the agent dumping raw
  node/edge lists into answers. One-line filter in
  `openai_tools.tool_definitions()`.
