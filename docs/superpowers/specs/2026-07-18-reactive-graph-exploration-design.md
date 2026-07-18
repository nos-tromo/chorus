# Reactive graph exploration — design

Date: 2026-07-18
Status: approved (brainstorming session)
Repos touched: `infra-ui`, `chorus` (docint migration explicitly deferred)

## Problem

The two chorus graph screens (`network_around`, `social_network_around`) render
one-shot Cytoscape snapshots: submit a form, the whole canvas is destroyed and
rebuilt, click only highlights locally, depth is hard-capped at 2, and there is
no way to explore outward from a rendered node. The agent screen never renders
graph payloads at all — `{nodes, edges}` results appear only as a JSON tool
trace. Users cannot investigate the graph themselves.

docint solves the reactivity half with a hand-written, dependency-free SVG
force-graph (`frontend/src/lib/forceGraph.ts` + `EntityGraph.tsx`): live
velocity-Verlet physics, drag-with-springing-neighbors, zoom-to-cursor,
selection with neighborhood dim, live filter/spread controls, maximize
overlay. It is airgap-ideal (zero dependencies) but still one-shot on the
data side.

## Decision summary

1. **Scope**: full rework — reactive canvas + incremental expand-on-click
   backend + inline graphs in the agent screen.
2. **Agent wiring**: option (a) — the `/agent/query` response carries full
   graph-tool result payloads; no frontend replay.
3. **Renderer**: extract docint's engine into a shared `@infra/ui`
   `<ForceGraph>` primitive. Chorus drops Cytoscape entirely.
4. **docint migration**: deferred to a later, separate PR. docint keeps its
   local implementation for now; the temporary duplication is accepted.
5. **Expand API**: two new `@audited` registry tools (six-file convention),
   not a generic tool or bespoke endpoint.
6. Judgment calls: expand affordance = button on selection panel +
   double-click shortcut (single click = select); the two tool screens stay
   separate; the depth-2 cap on the seed tools stays (expansion supersedes it).

## 1. `infra-ui`: `<ForceGraph>` primitive

- Port docint's `forceGraph.ts` near-verbatim (velocity-Verlet sim,
  phyllotaxis seeding, `fixNode`/`releaseNode` drag pinning, `reheat`,
  `setOptions`, settle-halting).
- New `<ForceGraph>` component generalized from docint's `EntityGraph.tsx`
  with an app-agnostic data model:
  - `nodes: { id, label, kind, size?, data? }[]`
  - `edges: { source, target, kind, directed?, weight?, dashed? }[]`
  - Styling via a consumer-supplied per-`kind` style map (fill color, radius
    scale, edge dash, arrowhead), not a hardcoded palette.
- **Directed edges** get SVG `<marker>` arrowheads (new — docint has none;
  chorus `FOLLOWS` needs them).
- **Theme-aware interior**: SVG (unlike canvas) consumes CSS custom
  properties. Edges, labels, and selection chrome bind to `@infra/ui` tokens
  and follow dark/light mode. Node fill palettes remain app-supplied.
- Built-in interactions (all from docint): zoom-to-cursor (non-passive wheel
  listener), pan, node drag with reheat, click/keyboard select
  (`role="button"`, Enter/Space) with neighborhood dim, maximize overlay
  (Escape exits, body-scroll lock), optional degree-filter and
  edge-length-spread controls.
- **New capability — incremental merge**: when the `elements` prop grows,
  existing nodes keep positions, new nodes seed near their anchor node, and
  the sim reheats. No full reseed. Plus `onNodeExpand(nodeId)` fired by the
  Expand affordance (selection-panel button; double-click shortcut).
- Delivery per infra-ui rules: `pnpm build`, commit `dist/`, vitest coverage,
  release tag `v0.2.0`. Chorus bumps its pinned git dependency.

## 2. chorus backend

Two new registry tools, each via the six-file convention (cypher in
`chorus/queries/`, Pydantic I/O in `chorus/tools/`, import in
`chorus/tools/__init__.py`, `tests/conftest.py` `_CHORUS_ENV_MODULES` entry,
frontend wiring, `tests/integration/` tests):

- **`expand_network_node(node_id, limit, topic_limit)`** — accepts the
  namespaced ids already emitted by `network_around` (`author:<id>`,
  `topic:<key>`); returns one hop of the author↔topic bipartite graph in the
  existing node/edge shapes plus `truncated`. No `seed`/`is_seed` semantics.
- **`expand_social_node(author_id, limit)`** — one hop of `FOLLOWS` /
  `FRIENDS_WITH` around the given author, flat (no `ring` field; the client
  assigns ring = parent's ring + 1 for sizing).

Both are agent-callable and §76-audited per invocation — every step of a
manual exploration lands in the audit log.

**Agent wiring (a)**: `/agent/query` trace entries gain an optional `result`
payload for graph tools, guarded by a size cap (existing `truncated` flag is
the signal when capped). Non-graph tools are unchanged.

`network_around` / `social_network_around` are unchanged, including the
depth-2 validator.

## 3. chorus frontend

- Delete `GraphCanvas.tsx`; remove `cytoscape` + `@types/cytoscape` from
  `frontend/package.json`. Rewrite `networkElements.ts` / `socialElements.ts`
  as pure mappers to the generic `<ForceGraph>` element shape.
- `ToolNetwork` / `ToolSocial` keep their seed forms; results feed an
  **explorer-state reducer** (`{nodes, edges}`, merge + dedup by node id)
  instead of piping the mutation result straight to the canvas. Expand
  actions call the new tools and merge. A selection side panel shows node
  details + the Expand action; truncation banners surface per expansion.
- **Agent screen**: trace entries carrying a graph payload render an inline
  `<ForceGraph>` card (with maximize), fed by the same explorer-state hook —
  agent answers become live, expandable graphs.
- New i18n keys (en/de) for all new captions; catalog parity test covers them.

## 4. Testing

- infra-ui: sim unit tests including merge-seeding behavior; component tests
  for zoom/pan/drag/select/expand affordances (jsdom-level, mock rAF).
- chorus frontend: mapper tests, explorer-reducer tests (merge/dedup/ring
  assignment), and route-component tests for both graph screens (closing an
  existing gap — the tabular tool screens have them, the graph screens do
  not).
- chorus backend: `tests/integration/test_expand_network_node.py` and
  `test_expand_social_node.py` against the Neo4j testcontainer; registry
  docstring test picks up the new tools; agent-response schema test for the
  `result` payload field and its size cap.

## 5. Sequencing

1. **PR 1 — infra-ui**: `<ForceGraph>` + `forceGraph.ts` + tests + `dist/` +
   release tag `v0.2.0`.
2. **PR 2 — chorus backend**: two expand tools + agent trace payload.
3. **PR 3 — chorus frontend**: dep bump to `@infra/ui` `v0.2.0`, adopt
   `<ForceGraph>`, explorer state, expand wiring, agent inline graphs,
   Cytoscape removal.

Plus an ADR in `chorus/docs/decisions/` recording Cytoscape → shared
ForceGraph and the incremental-expansion model.

## Out of scope

- docint's migration onto the shared primitive (follow-up PR in docint).
- Lifting the depth cap on the seed tools.
- Merging the two graph screens into one explorer.
- Persisted layouts / graph export.
