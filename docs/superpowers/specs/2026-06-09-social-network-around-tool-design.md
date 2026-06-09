# `social_network_around` tool — design

**Date:** 2026-06-09
**Status:** approved (design)
**Scope:** add one graph-only retrieval tool,
`social_network_around(author, depth, limit, second_ring_limit)`, that returns
the **author ego network** over the *social* graph (`:FOLLOWS` / `:FRIENDS_WITH`)
as a node/edge list for visualization. It is the social twin of
`network_around` (which is the *topic* ego network) and the first tool to
traverse the social graph.

## Context & goal

Chorus ingests a social graph — `(:Author)-[:FOLLOWS]->(:Author)` (directed) and
`(:Author)-[:FRIENDS_WITH]-(:Author)` (undirected, one edge per pair, ADR 0007) —
but **no retrieval tool touches those edges**. Every existing graph tool
traverses the *mention* leg (`(:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->(:Alias)→(:Entity)`);
"connected authors" today means topic-shared (`authors_connected_by_topic`), not
socially linked. ADR 0007's Consequences section names "friend-of-friend
traversals" and an author ego-network as *structurally unblocked*, and the
`network_around` design doc (`2026-06-04-network-around-tool-design.md`,
*Deferred*) defers exactly this:

> **Author–author edges** (co-authorship / `FOLLOWS` / `FRIENDS_WITH`) … layered
> onto the same picture. Trigger: a request to see the *social* graph, not just
> the topic-bipartite projection. Additive node/edge kinds; the output model
> already carries a `kind` field.

This is that follow-up. The new tool reuses `network_around`'s renderer-agnostic
node/edge contract and the same airgap-safe `st.graphviz_chart` UI track; what is
new is the **seed type** (an author, not a topic) and the **edges traversed**
(social ties, not mentions). The query stays in version-controlled Cypher; the
agent never writes Cypher.

## What the network *is* (the load-bearing design choice)

The **author ego network** centered on a seed author: concentric rings of the
authors socially tied to them.

- **Nodes** are all `:Author`. Each carries its `ring` (0 = seed, 1 = direct
  ties, 2 = ties-of-ties) and `is_seed`.
- **Edges** are social ties between authors, each tagged with `kind`
  (`"follows"` | `"friends"`) and `directed` (true for follows, false for
  friends).

Rings from the seed author **S**:

| `depth` | rings included | shape |
|---|---|---|
| 1 | S + its direct ties (followers, following, friends) | the ego star |
| 2 | the above + the ties of those neighbours | the 2-hop neighbourhood |

v1 supports `depth ∈ {1, 2}`; `depth > 2` is rejected at input validation
(HTTP 422), mirroring `network_around`'s `_depth_supported` and
`authors_connected_by_topic`'s `max_hops` posture ("ship the low hop count in v1;
deeper traversal is a documented stretch, rejected rather than silently
degraded"). Connections can dwarf the artifact tables (ADR 0007), so bounded
depth is also a cost guard.

v1 emits **radial** edges only — seed↔ring-1 and ring-1↔ring-2. Edges *among*
nodes of the same ring (e.g. mutual friendships between the seed's friends) are
deferred (see *Deferred*); the output contract already supports adding them.

## Edge handling — both types, direction kept on edges (load-bearing)

Decided with the user. The neighbourhood is built by expanding **both** edge
types in **both** orientations, so "socially adjacent" means any tie:

- `(seed)-[:FOLLOWS]->(nb)` — seed follows nb (`kind=follows`, `directed=true`,
  emitted source=seed → target=nb).
- `(seed)<-[:FOLLOWS]-(nb)` — nb follows seed (`kind=follows`, `directed=true`,
  emitted source=nb → target=seed).
- `(seed)-[:FRIENDS_WITH]-(nb)` — undirected (`kind=friends`, `directed=false`,
  emitted in canonical lower-id-first order so the same pair never yields two
  edges).

Keeping `directed` on each edge lets the renderer draw arrowheads for follows and
plain lines for friends — one simple "show everyone adjacent" semantic, no
information lost. A caller-selected `direction` filter (following / followers /
friends only) was considered and **deferred** — the whole neighbourhood is the
right v1 default and keeps the agent-facing schema small.

**No edge weight in v1.** Social edges carry only `crawled_at` (ADR 0007); the
per-pair engagement columns are in the raw store but not projected to the graph.
Weighting/recency is a non-breaking later addition (see *Deferred*).

## Matching semantics — seed by author identity

The `author` string is matched against `:Author` the same way
`authors_connected_by_topic` matches its `seed_author`: for a trimmed, case-folded
`q`,

```
toLower(coalesce(seed.handle, "")) = q OR toLower(coalesce(seed.display_name, "")) = q
```

A name can match several authors (handle/display-name collisions). An ego network
needs a single ego, so the tool picks one **deterministically** — prefer a handle
match over a display-name-only match, then lowest `:Author.id` — and the rare
ambiguity is documented, mirroring `network_around`'s `_pick_seed` honesty note.
(The tabular `authors_connected_by_topic`, which can return many egos, groups
per seed instead; a drawn ego network cannot.)

## Bounding (a viz must be bounded, and so must the query)

Two deterministic caps, applied in Cypher, mirroring `network_around`'s
`limit`/`topic_limit` pair:

- `limit: int = 25` (1..200) — max **ring-1** neighbours.
- `second_ring_limit: int = 50` (1..500) — max distinct **ring-2** neighbours
  (depth 2 only).

Within each ring, neighbours are ranked by **social degree desc**
(`count{ (nb)-[:FOLLOWS|FRIENDS_WITH]-() }` — the hubs, using Neo4j's degree
store), tiebreak `:Author.id` asc, then sliced to the cap. `truncated: bool` is
set true when either cap dropped nodes, so the UI can disclose a capped view —
consistent with chorus's rule to surface incompleteness rather than hide it.

## Tool spec

One Cypher template `queries/social_network_around.cypher` (never inline) + one
module `tools/social_network_around.py` (`@register_tool` + `@audited` + Pydantic
in/out) + one Streamlit page `ui/pages/08_social_network_around.py` with a pure
`ui/social_network_dot.py` DOT builder. The Cypher below is illustrative; exact
Cypher is finalized in implementation.

### Input — `SocialNetworkAroundIn`

- `author: str` — seed handle or display name, case-insensitive (rule above).
- `depth: int = 1` (1..2) — `depth > 2` rejected via `field_validator` (→ 422).
- `limit: int = 25` (1..200) — ring-1 cap.
- `second_ring_limit: int = 50` (1..500) — ring-2 cap (depth 2 only).

### Output — `SocialNetworkAroundOut`

Renderer-agnostic graph with namespaced node ids (`"author:<id>"`) so any renderer
consumes them directly:

- `seed: str` — matched seed label (handle/display_name), else the trimmed query.
- `seed_node_id: str | None` — `"author:<id>"`, or `None` when nothing matched.
- `nodes: list[SocialNode]`: `id`, `label` (handle ?? display_name ?? id),
  `ring: int` (0/1/2), `is_seed: bool`.
- `edges: list[SocialEdge]`: `source`, `target` (node ids), `kind:
  Literal["follows","friends"]`, `directed: bool`.
- `truncated: bool`.

Audit hooks:

- `audit_result_count()` → `len(nodes)` (network size), as `network_around`.
- `audit_entities()` → **distinct author ids in the network** (seed + neighbours).
  *Deliberate divergence* from the mention tools, which record resolved
  `:Entity` ids: a social read touches no `:Entity`, and the §76-relevant
  "entities touched" for it are the **persons** whose connection data was
  accessed. Documented in the code so the audit column's mixed id-space is
  intentional, not accidental.

### Traversal sketch

```cypher
// Seed: one ego, deterministic on ambiguity (handle match preferred, then id).
MATCH (seed:Author)
WHERE toLower(coalesce(seed.handle,"")) = toLower(trim($author))
   OR toLower(coalesce(seed.display_name,"")) = toLower(trim($author))
WITH seed
ORDER BY (toLower(coalesce(seed.handle,"")) = toLower(trim($author))) DESC, seed.id ASC
LIMIT 1
// Ring 1: direct ties over the three legs, each carrying kind/directed/src/dst,
//   ranked by degree desc (tiebreak id), capped at $limit.
// Ring 2 ($depth >= 2): ties of the retained ring-1 nodes to new authors,
//   ranked + capped at $second_ring_limit.
// RETURN seed identity, ring1[], ring2[], edges[] (namespaced ids,
//   kind/directed), and the truncated flags — assembled into nodes/edges
//   in the tool module (single-row assembly, like network_around).
```

## Visualization — reuse the `network_around` track

A new pure `ui/social_network_dot.py::to_dot(result)` builds a DOT `digraph` from
the tool's `nodes`/`edges`, passed to `st.graphviz_chart` (Streamlit's bundled
viz.js — no system `graphviz` binary, no CDN, no runtime network call; the same
airgap-safe choice `network_around` made and verified). Styling: seed highlighted,
nodes coloured by `ring`; **follows edges drawn with arrowheads, friends edges
with `dir=none`** (off the `directed` flag). The DOT builder is a pure function →
unit-tested with no Streamlit runtime.

> **Implementation must verify** (airgap gate, as `network_around`):
> `st.graphviz_chart(dot)` renders with no system graphviz binary installed and
> issues no network request.

## What carries over for free (no changes needed)

- **Dispatch is registry-driven.** `api/routers/tools.py` builds `GET /tools` /
  `POST /tools/{name}` from `TOOLS`; the agent builds its tool list from the same
  registry (`agent/openai_tools.py`). Registering surfaces the tool to both — no
  router, client, or agent edits.
- **UI client is generic.** `ChorusClient.call_tool(name, payload)` already
  returns the JSON output.
- **Audit.** `@audited` writes exactly one row per call from `audit_entities()` +
  `audit_result_count()`.
- **No new migration.** `Author.id` uniqueness (migration 001) and the
  `:FOLLOWS` / `:FRIENDS_WITH` `crawled_at` indexes (migration 002) already exist;
  traversal is backed by the relationship-type + node-id degree store.

### Agent exposure

Registration auto-exposes the tool to the NL agent. A node/edge dump narrates
poorly, so — as `network_around` — keep it exposed but steer with a first
docstring line: use it when a *social network / connections graph* is asked for,
and report **summary** facts (network size, central authors), not the raw graph.
The `agent_exposed` flag deferred for `network_around` would also cover this tool.

## Testing

`tests/integration/test_social_network_around.py` against an ephemeral Neo4j over
a small `:FOLLOWS`/`:FRIENDS_WITH` fixture. Assert:

- **depth-1 star** — seed + its direct ties; the three legs (follows-out,
  follows-in, friends) appear with correct `kind`/`directed`; friends edge in
  canonical order; no ring-2 nodes.
- **depth-2 expansion** — ties-of-ties appear with ring-1↔ring-2 edges; the
  depth-1 star edges are still present (radial superset); seed excluded from
  ring 2.
- **bounding** — `limit` caps ring 1 and `second_ring_limit` caps ring 2
  deterministically (by degree); `truncated` flips true exactly when a cap bites.
- **`depth > 2` → `ValidationError`** (→ 422).
- **ambiguous name** — two authors share a display name → one ego picked
  deterministically (handle match preferred, then lowest id).
- **thin-author neighbour** — a follower that never posted (no `:AUTHORED`) still
  appears as a node (connections MERGE thin `:Author`s).
- **empty seed** — no match → empty `nodes`/`edges`, `seed_node_id = None`,
  `truncated = False`.
- **audit** — `audit_result_count()` = node count; `audit_entities()` carries the
  network's author ids; one `ok` row per call.

Pure graph read — the inference provider is untouched, so no inference stubbing.

A **UI unit test** `tests/ui/test_social_network_dot.py` for the DOT builder:
seed highlighted, a `follows` edge rendered with an arrowhead and a `friends`
edge with `dir=none`, quote-escaping, empty network valid.

## Build sequence (TDD)

1. This design doc.
2. `tests/integration/test_social_network_around.py` — red first.
3. `chorus/queries/social_network_around.cypher`.
4. `chorus/tools/social_network_around.py` (`@register_tool` + `@audited` +
   Pydantic).
5. `chorus/tools/__init__.py` — add the import line (self-registers → REST +
   agent) **and** add the module to `tests/conftest.py::_CHORUS_ENV_MODULES`
   (omitting it makes the tool vanish from `TOOLS` in later tests — the
   documented gotcha).
6. `tests/ui/test_social_network_dot.py` red → `chorus/ui/social_network_dot.py`
   + `chorus/ui/pages/08_social_network_around.py` + `social.*` keys in
   `utils/ui_strings.py` (both `en` and `de` — import-time parity check). Verify
   the airgap render gate.

No router/client/agent/prompt edits.

## Resolved decisions (defaults baked in)

- **Shape:** author ego network over the social graph (not path-finding or
  mutual-connections — both rejected with the user).
- **Edges:** both `:FOLLOWS` (both directions) and `:FRIENDS_WITH` (undirected),
  direction kept per-edge (`kind` + `directed`). No weight in v1.
- **Matching:** seed by handle/display_name case-insensitively; single ego picked
  deterministically on ambiguity.
- **Depth:** v1 `{1, 2}`, default 1, reject `> 2` at validation; UI slider
  defaults to 2.
- **Bounding:** `limit` + `second_ring_limit`, ranked by social degree, in Cypher,
  deterministic; `truncated` disclosed.
- **Renderer:** `st.graphviz_chart` + DOT — zero new dependencies, airgap-safe.
- **Audit:** `audit_entities()` records author ids (deliberate, documented).
- **No ADR warranted** — a tool within the existing, ADR-backed retrieval pattern,
  reversing no load-bearing choice and adding no dependency. Spec lives in
  `docs/superpowers/specs/`, like `network_around`.

## Deferred (with trigger)

- **Intra-ring edges** (mutual ties among neighbours — clustering/triangles).
  Trigger: community-structure questions. Additive edges; contract already
  supports it.
- **Edge weighting / recency** (engagement columns or `crawled_at`). Trigger:
  "strongest ties first". Non-breaking edge-property migration.
- **`depth > 2` / variable-depth BFS.** Trigger: a concrete multi-hop ask; needs
  cost work given connection volume.
- **`direction` filter param** (following / followers / friends / all). Trigger:
  targeted "who does X follow" questions.
- **Caller-tunable bounds via `env_cfg`.** Trigger: operators retuning limits
  without a redeploy; v1 keeps Pydantic defaults like `network_around`.
- **`agent_exposed` flag on `ToolSpec`.** Trigger: the agent dumping raw node/edge
  lists into answers. Shared with `network_around`.
