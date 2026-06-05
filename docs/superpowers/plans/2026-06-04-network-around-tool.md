# `network_around` tool — implementation plan

> **For agentic workers:** implement task-by-task with TDD (red → green →
> commit). Steps use checkbox (`- [ ]`) syntax. Integration tests need Docker
> running (testcontainers boots Neo4j 5.26.26).

**Status:** ✅ implemented on `claude/network-around-tool-design-4KSKN`. Tool +
Cypher + UI page + tests landed; ruff + ruff-format + mypy clean; offline-runnable
tests (DOT-builder unit tests, registry, `depth > 2` validation) green. The Neo4j
integration tests (`tests/integration/test_network_around.py`) require Docker and
run in CI — they could not be executed in the authoring sandbox (no Docker daemon).

**Goal:** Add one graph-only retrieval tool — `network_around` — that returns the
**bipartite Author↔Topic ego network** around a seed entity as a renderer-ready
node/edge list, plus chorus's first **visualization** UI page (drawn, not
tabular). Clones the established `posts_mentioning` / `authors_mentioning` tool
pattern; the only genuinely new surface is the DOT-rendered Streamlit page.

**Architecture:** one Cypher template in `chorus/queries/` + one module in
`chorus/tools/` (`@register_tool` + `@audited` + Pydantic in/out) + one Streamlit
page in `chorus/ui/pages/`. No router/UI-client/agent edits — `GET /tools`,
`POST /tools/{name}`, `ChorusClient.call_tool`, and `agent/openai_tools` are
already generic.

**Tech stack:** Python 3.12, FastAPI, Neo4j (Cypher), Pydantic, Streamlit
(`st.graphviz_chart` — DOT string, **no new dependency**), pytest +
testcontainers, ruff, mypy. Package manager: `uv`.

**Approved spec:** `docs/superpowers/specs/2026-06-04-network-around-tool-design.md`
(read it first — it pins the network shape, matching semantics, bounding, and the
airgap renderer call).

---

## Context

Five graph tools ship today, all tabular. `network_around` was deferred from the
round-1 graph-tools work explicitly because "its value is a network
visualization, a different UI track than tabular result pages." This is that
work. The traversal already exists (the
`(:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->(:Alias)→(:Entity)` leg behind
`authors_mentioning` / `topic_co_occurrence` / `authors_connected_by_topic`); new
here is returning a **graph** and **drawing** it.

## The load-bearing constraints (from the spec)

- **Network = bipartite Author↔Topic ego graph** seeded on the topic. Topic
  identity = `coalesce(entity, alias)` (resolved `:Entity.id` else `:Alias`
  surface form), so it improves automatically when resolution runs — no tool
  change. Edge = Author→Topic, `weight` = `count(DISTINCT post)`.
- **Rings:** depth 1 = seed + authors mentioning it (star); depth 2 = + the other
  topics those authors mention (topic→author→co-topic). v1 supports `{1, 2}`;
  `depth > 2` → 422.
- **Matching mirrors `posts_mentioning` / `authors_mentioning` verbatim** (the
  parenthesised `Entity`/`Alias`/`RESOLVED_TO` clause). Depth-1 author set ==
  `authors_mentioning(X)` (lockstep, cross-tool tested).
- **Bounded:** `limit` caps the author ring (by seed mention-count), `topic_limit`
  caps second-ring topics (by total weight). Both in Cypher, deterministic;
  `truncated` flag disclosed.
- **Airgap:** renderer makes **no runtime network call**. v1 = `st.graphviz_chart`
  with a DOT string (zero new deps, client-side viz.js Streamlit already bundles).

## Shared tool recipe (study `chorus/tools/authors_mentioning.py` first)

1. Pydantic input model; `depth` with `field_validator` rejecting `> 2`
   (copy the shape of `authors_connected_by_topic`'s `max_hops` validator).
2. Pydantic output model(s); top-level implements `audit_entities() -> list[str]`
   (distinct non-null topic `entity_id`s) and `audit_result_count() -> int`
   (= node count), both read by `@audited`.
3. Function signature `def tool(driver, params, *, user, audit) -> OutModel:` with
   `del user, audit`.
4. Decorators outer→inner: `@register_tool(...)` then `@audited`.
5. Load Cypher via `load_template("network_around")`; run in
   `with driver.session() as session:`. Build namespaced node ids
   (`f"topic:{key}"`, `f"author:{id}"`) in Python from the rows.

### Wiring (two edits — the second prevents a real registry bug)

- **`chorus/tools/__init__.py`** — add `network_around` to the self-registering
  import block so it lands in `TOOLS`.
- **`tests/conftest.py`** — add `"chorus.tools.network_around"` to
  `_CHORUS_ENV_MODULES` (before the `"chorus.tools"` entry). That list is evicted
  from `sys.modules` before each test so `TOOLS` rebuilds consistently with
  `_audit`. Omitting the module makes the tool **missing from `TOOLS`** in any
  test running after another test imported it.

### Formatting gotcha (from the round-1 plan)

`pre-commit run --all-files` only checks git-**tracked** files. **Stage new files
(`git add`) before running pre-commit** so ruff-format normalizes them in the same
task rather than the next one.

---

## Task 1 — `network_around` tool (Cypher + module + wiring + tests)

**Files**: `chorus/queries/network_around.cypher`, `chorus/tools/network_around.py`,
modify `chorus/tools/__init__.py` + `tests/conftest.py`,
`tests/integration/test_network_around.py`.

- [ ] **Red** — write `tests/integration/test_network_around.py` with the spec's
  assertions:
  - depth-1 star (seed topic + author nodes; all edges author→seed; no ring-2
    topics);
  - depth-1 lockstep with `authors_mentioning` (same author set, no cap pressure);
  - depth-2 expansion (co-topics + author→topic edges; seed star still present);
  - resolved + unresolved both match (seed by canonical name and by surface form;
    topic `entity_id` set/null; `audit_entities()` resolved-only);
  - bounding (`limit` caps authors, `topic_limit` caps co-topics by weight;
    `truncated` true/false correctly);
  - `depth > 2` → `ValidationError`;
  - no-merge (two same-named authors = two nodes);
  - empty seed (empty `nodes`/`edges`, `seed_node_id=None`, `truncated=False`);
  - registered in `TOOLS`.
  Use `migrated_driver` + `in_memory_audit` fixtures and a small alias-based
  fixture graph with at least one resolved `:Entity`. Confirm red.

- [ ] **Cypher** — `chorus/queries/network_around.cypher`. Ring-1: match authors
  mentioning the seed (parenthesised `posts_mentioning` clause), aggregate
  `w_seed = count(DISTINCT p)` + a stable seed key/label/entity_id via
  `head(collect(...))`, `ORDER BY w_seed DESC, a.id ASC LIMIT $limit`. Ring-2
  (gated `$depth >= 2`): from the retained authors, expand
  `(a)-[:AUTHORED]->(:Post)-[:MENTIONS]->(m2)` → topic key/label/entity_id, edge
  `weight = count(DISTINCT post)` per (author, topic); keep top `$topic_limit`
  topics by total weight. RETURN assembles raw rows the Python layer turns into
  `nodes`/`edges`; also return enough to compute `truncated` (e.g. pre-cap vs
  post-cap counts, or a boolean per cap). Keep all caps/ordering in Cypher
  (auditable, deterministic). Reuse the `AND`/`OR` parenthesisation exactly.

- [ ] **Module** — `chorus/tools/network_around.py`:
  - `NetworkAroundIn(entity: str, depth: int = Field(1, ge=1), limit: int =
    Field(25, ge=1, le=200), topic_limit: int = Field(50, ge=1, le=500))` with a
    `field_validator("depth")` rejecting `> 2` and
    `model_config = {"populate_by_name": True}`.
  - `NetworkNode(id, kind, label, entity_id, is_seed)`,
    `NetworkEdge(source, target, weight)`,
    `NetworkAroundOut(seed, seed_node_id, nodes, edges, truncated)` with
    `audit_entities()` (distinct non-null topic `entity_id`s, first-seen order)
    and `audit_result_count()` (= `len(nodes)`).
  - `@register_tool(name="network_around", …)` then `@audited`; first docstring
    line = the agent-facing description from the spec ("Return the author↔topic
    network around an entity …"). Build namespaced node ids in Python.

- [ ] **Wire** — add the import to `chorus/tools/__init__.py` and the module path
  to `tests/conftest.py::_CHORUS_ENV_MODULES`.

- [ ] **Green** — `uv run pytest tests/integration/test_network_around.py`.

- [ ] **Commit** `feat(tools): add network_around graph query tool`.

---

## Task 2 — visualization UI page (the new track)

**Files**: `chorus/ui/pages/06_network_around.py`, a small DOT-builder unit test
(e.g. `tests/ui/test_network_around_dot.py`).

> First **drawn** result page. Renderer = `st.graphviz_chart(dot_string)` — no new
> dependency, no runtime network call.

- [ ] **Airgap render gate (verify first)** — confirm `st.graphviz_chart` renders
  a DOT string with **no system `graphviz` binary** installed and issues **no
  outbound request** (client-side viz.js bundled in Streamlit). If false: emit the
  DOT for download and open a dependency review for `streamlit-agraph` — **do not**
  use a CDN renderer (`pyvis` default is disqualified). Record the finding in
  `docs/airgap.md` either way.

- [ ] **DOT builder (pure function, unit-tested)** —
  `_to_dot(out: dict) -> str` mapping `nodes`/`edges` to DOT: seed node
  highlighted, topic vs author nodes styled distinctly, edge `weight` → pen
  width/label. Unit test asserts the seed node is highlighted and one edge per
  input edge is emitted — no Streamlit runtime needed.

- [ ] **Page** — `06_network_around.py` mirroring `01_posts_mentioning.py`'s
  `@st.cache_resource` client + form: `entity` text input, `depth` slider
  (default **2**, 1..2), `limit`/`topic_limit` sliders; call
  `client.call_tool("network_around", payload)`; render `_to_dot(result)` via
  `st.graphviz_chart`. Show node/edge counts, a caption that the view is **capped**
  and discloses `truncated`, and the standing topic-clustering note. Empty-network
  → `st.info`.

- [ ] **Commit** `feat(ui): add network_around visualization page`.

---

## Final verification

- [ ] `uv run pytest` — full suite green (new integration + DOT-builder tests).
- [ ] `uv run pre-commit run --all-files` — ruff (lint+format) + mypy clean
  (stage new files first).
- [ ] `GET /tools` exposes `network_around`; `depth > 2` rejected at input
  validation (422 via the router's `ValidationError` handling).
- [ ] Manual UI smoke (optional, needs Neo4j + API up): page 06 draws the network;
  verify the airgap render gate held (no network tab requests on render).

## Notes

- No changes to `chorus/api/routers/tools.py`, `chorus/ui/client.py`, or
  `chorus/agent/openai_tools.py` — dispatch, the UI client, and agent tool-listing
  are generic; registration surfaces the tool everywhere.
- Landing nothing else: resolution has already shipped, so topic nodes carry
  `entity_id` where resolved; the `coalesce` path handles unresolved aliases with
  no toggle.
- The node/edge output is renderer-agnostic — swapping the DOT renderer for an
  interactive component later (behind dependency review) is a UI-only change.
