# Unified graph explorer — design

Date: 2026-07-19
Status: approved (brainstorming session)
Repos touched: `infra-ui` (additive API, v0.5.0), `chorus` (frontend only)
Predecessor: `2026-07-18-reactive-graph-exploration-design.md` and ADR 0016,
whose "Future: unified explorer" section recorded the enablers this design
now uses.

## Problem

chorus has two graph screens — the entity/topic network (`network_around`)
and the social ego network (`social_network_around`) — each with its own
route, hook, mapper, and style map. An investigation that crosses families
(who talks about X, and how are those people socially connected?) forces the
analyst to hop screens and lose the canvas. The enablers for one canvas were
built deliberately: both families share the `author:<id>` node id space, the
expand tools are node-scoped, edge kinds don't collide, and the renderer is
family-agnostic.

## Decisions (user-approved)

1. **Replace both screens** with a single "graph explorer" nav item; the two
   old routes, screens, hooks, and the social style map are deleted.
2. **Two expand buttons per author** — the analyst chooses which neighborhood
   to pull ("Expand topics" / "Expand ties"); topics have one ("Expand
   mentions"). Requires a small additive infra-ui API.
3. **Seed form = type selector** (segmented Entity | Author) swapping the
   input label and limit fields; submit calls the matching existing seed
   tool. Zero new endpoints.
4. Judgment call (accepted): depth/limit fields stay exactly as on the old
   screens, per seed type, unchanged semantics.

## 1. infra-ui: additive `expandActions` API (v0.5.0)

New optional props on `ForceGraph`, superseding the single expand affordance
when present:

```ts
expandActions?: Array<{ id: string; label: string }>
onExpandAction?: (actionId: string, nodeId: string) => void
```

- Rendered exactly like the current Expand button (bottom-left chip row),
  only when exactly one node is selected — one button per action.
- The consumer recomputes `expandActions` per selection, so the action list
  can differ by node kind (chorus: two for authors, one for topics).
- Double-click on a node fires the FIRST action.
- `expandingId` keeps its meaning (all action buttons disabled while set).
- `onExpandNode` is unchanged and still works when `expandActions` is absent
  — purely additive; docint unaffected. When both are supplied,
  `expandActions` wins (documented).
- Released as v0.5.0 (hand-cut tag, as established for this repo).

## 2. chorus: unified state

- **Kinds**: `{seed, author, topic}` — amber `#fbbf24`, violet `#7c3aed`
  (label `#a78bfa`), green `#4ade80`. **Ring semantics retire**; the ring
  palette and ring-assignment logic are deleted with the social screen.
- **Node sizing**: topics by incident `mentions` weight (as today); authors
  by incident edge count in view (degree over all edge kinds), seed floor 6.
- **Unified model**: nodes `{id, kind, label, entity_id?}`; edges union —
  `mentions {weight}` (solid), `follows {directed}` (arrowhead),
  `friends` (dashed). `mergeGraph` unchanged; edge key
  `source__target__kind` throughout.
- **`useUnifiedExplorer`** replaces both per-family hooks:
  - `seedFrom(out)` accepts either seed tool's payload, mapped to the
    unified model (ring info discarded; seed flagged).
  - `expandTopics(authorId)` → `expand_network_node(author:<id>)`
  - `expandTies(authorId)` → `expand_social_node(<id>)`
  - `expandTopic(topicId)` → `expand_network_node(topic:<key>)`
  - Carried over unchanged in behavior: multiselect (`selectedIds`,
    `select`), `removeNodes` (view-only), the in-flight anchor guard
    (expansion results for removed anchors are discarded), `expandingId`,
    `expansionTruncated`, `expandError`.
  - Backend: **zero changes** — the five graph tools and their §76 audit
    behavior are untouched.

## 3. The screen

`frontend/src/routes/ToolExplorer.tsx` replaces `ToolNetwork.tsx` +
`ToolSocial.tsx`:

- Segmented control Entity | Author; per-type input + depth/limit fields
  (entity: depth 1–2, author limit 1–200, topic limit 1–500; author: depth
  1–2, limit 1–200, second-ring limit 1–500 — the old screens' fields
  verbatim).
- One `ForceGraph`: union node/edge style maps; merged legend (Seed,
  Authors, Topics); `expandActions` computed from the selected node's kind;
  `statusText` hint; multiselect/removal as today.
- Exports: JSON / GraphML / HTML buttons over the unified graph, filenames
  `chorus-explorer.{json,graphml,html}`.
- Banners: seed truncation, expansion cap, expansion error — existing keys
  where reusable.
- Router/Sidebar: one NETWORKS item ("graph explorer", route
  `/tools/explorer`); the two old routes are removed (no redirects — decided
  with "replace both").
- i18n: new `explorer.*` keys (title, caption, seed-type labels, expand
  actions, counts) in en/de; keys that die with the old screens are removed
  (parity test enforces).

## 4. Untouched

- Backend: all five graph tools, audit posture, agent loop.
- Agent inline cards (`AgentGraphCard`): UX and per-tool payload seeding
  unchanged, but internals migrate to the unified hook/mapper — the card
  consumed the per-family hooks/mappers, which this design deletes, so it
  must ride the unified machinery (and thereby gains the per-kind expand
  actions for free). (A future "open in explorer" hand-off remains an idea,
  not designed.)
- docint (consumes v0.4.0 semantics; v0.5.0 is additive).

## 5. Testing

- infra-ui: TDD `expandActions` — rendering/gating at exactly-one-selected,
  per-action firing, double-click = first action, disabled while expanding,
  `onExpandNode` regression (absent `expandActions` behaves exactly as
  v0.4.0), precedence when both supplied.
- chorus: hook tests — seed from both payload shapes; the three expansions
  merging into ONE canvas; the payoff case: seed entity → expand an
  author's ties → the same author node carries both mention and social
  edges (id-space dedup); removal/race/truncation carried over. Route tests
  — seed-type switch swaps fields and calls the right tool; per-kind expand
  buttons; exports; deletion verified (`grep` finds no ToolNetwork/
  ToolSocial/useNetworkExplorer/useSocialExplorer/ring references).
- ADR 0016 addendum: "Future: unified explorer" → implemented, with the
  kind-precedence resolution (rings retired) recorded.

## 6. Sequencing

1. **PR A — infra-ui**: `expandActions` + tests + v0.5.0 (approval-gated
   merge + hand-cut tag).
2. **PR B — chorus**: pin bump, unified hook/mapper/screen, deletions,
   i18n, ADR addendum, tests.

## Out of scope

- Agent-card unification / "open in explorer" hand-off.
- New backend endpoints (smart seed search, multi-seed union).
- Redirects from the removed routes.
- Lifting depth caps; persisted layouts; legend-click filtering.
