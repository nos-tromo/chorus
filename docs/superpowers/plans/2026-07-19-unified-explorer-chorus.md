# Unified Graph Explorer (chorus) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two graph screens with one unified explorer whose canvas holds both graph families (mention + social edges) with per-kind dual expansion; agent cards ride the same machinery.

**Architecture:** A unified node/edge model (`explorerElements.ts`) and one `useUnifiedExplorer` hook supersede the per-family pair; a new `ToolExplorer` screen with an Entity|Author seed selector replaces `ToolNetwork`/`ToolSocial`; `AgentGraphCard` migrates internally. Backend untouched (all five graph tools as-is).

**Tech Stack:** React 19, TS, `@infra/ui#v0.5.0` (`expandActions`), @tanstack/react-query, vitest.

**Repo:** `/Users/himarc/dev/nos-tromo/infra/chorus`, branch `feature/unified-explorer` off `main`. **Prerequisite:** infra-ui `v0.5.0` tag exists.

## Global Constraints

- Data confidentiality: synthetic fixtures only.
- Backend untouched: no changes under `chorus/`, no new endpoints; the five graph tools and audit behavior are frozen.
- Frontends stay thin: merge/kind logic is view state.
- All user-visible strings via the i18n catalog (en/de parity test).
- `@infra/ui` pinned to the `v0.5.0` tag.
- After this plan: `grep -ri "ToolNetwork\|ToolSocial\|useNetworkExplorer\|useSocialExplorer\|networkElements\|socialElements" frontend/src` returns nothing; no orphaned i18n keys.
- `make verify` + full `uv run pytest` green before push. Do not push mid-plan; the controller pushes/opens the PR after the final task.
- Reference for every wiring pattern: the current `ToolNetwork.tsx` / `ToolSocial.tsx` / `useGraphExplorer.ts` / `AgentGraphCard.tsx` — read before each task; they are the code being generalized.

---

### Task 1: Pin bump + unified model & mapper

**Files:**
- Modify: `frontend/package.json` (+lockfile) — `@infra/ui` → `v0.5.0`
- Create: `frontend/src/lib/explorerElements.ts` + `.test.ts`

**Interfaces (produces — later tasks consume verbatim):**

```ts
export interface ExplorerNode {
  id: string                      // author:<id> | topic:<key>
  kind: 'author' | 'topic'
  label: string
  entity_id: string | null
  is_seed: boolean
}
export interface ExplorerEdge {
  source: string
  target: string
  kind: 'mentions' | 'follows' | 'friends'
  weight?: number
  directed?: boolean              // true only for follows
}
export const EXPLORER_NODE_STYLES: Record<string, ForceGraphNodeStyle>
  // seed {color:'#fbbf24'}, author {color:'#7c3aed', labelColor:'#a78bfa'}, topic {color:'#4ade80'}
export const EXPLORER_EDGE_STYLES: Record<string, ForceGraphEdgeStyle>
  // mentions {opacity:0.6}, follows {opacity:0.7}, friends {dashed:true}
export const explorerEdgeKey = (e: ExplorerEdge) => `${e.source}__${e.target}__${e.kind}`
export function toExplorerForceGraph(g: GraphState<ExplorerNode, ExplorerEdge>): {
  nodes: ForceGraphNode[]; edges: ForceGraphEdge[]
}
// node.kind = is_seed ? 'seed' : n.kind
// topic size = 1 + sum of incident mentions weight (seed floor 6)
// author size = 1 + incident edge COUNT over all kinds (seed floor 6)
// edge passthrough {source,target,kind,weight,directed}
```

- [ ] **Step 1** Bump pin (`v0.4.0` → `v0.5.0`), `pnpm install`, `pnpm ls @infra/ui` = 0.5.0. Commit `build(frontend): @infra/ui v0.5.0`.
- [ ] **Step 2** TDD the mapper (~10 tests, mirroring `networkElements.test.ts` style): kind derivation incl. seed override; both sizing rules incl. seed floor and mixed-kind author degree; edge passthrough per kind; `explorerEdgeKey` distinguishes kinds; determinism; pure (inputs unmutated). RED → implement → GREEN.
- [ ] **Step 3** Lint/tsc clean on new files; commit `feat(frontend): unified explorer node/edge model + mapper`.

---

### Task 2: `useUnifiedExplorer`

**Files:**
- Create: `frontend/src/hooks/useUnifiedExplorer.ts` + `.test.tsx`

**Interfaces (produces):**

```ts
export function useUnifiedExplorer(): {
  graph: GraphState<ExplorerNode, ExplorerEdge> | null
  seedFromNetwork: (out: NetworkAroundOut) => void
  seedFromSocial: (out: SocialNetworkAroundOut) => void
  expandTopics: (authorNodeId: string) => void   // expand_network_node {node_id}
  expandTies: (authorNodeId: string) => void     // expand_social_node {author_id: id minus 'author:'}
  expandTopic: (topicNodeId: string) => void     // expand_network_node {node_id}
  selectedIds: string[]
  select: (ids: string[]) => void
  removeNodes: (ids: string[]) => void
  expandingId: string | null
  expansionTruncated: boolean
  expandError: string | null
}
```

Behavior (generalized verbatim from `useGraphExplorer.ts` — read it first; same EXPAND_LIMIT=50, same errText, same busy-guard ref, same onSettled):
- `seedFromNetwork`: nodes map 1:1 (already `{id,kind,label,entity_id,is_seed}`), edges → kind `'mentions'` with weight. `seedFromSocial`: nodes → `{id, kind:'author', label, entity_id:null, is_seed}` (ring DISCARDED), edges pass through with kind/directed. Both reset selection/truncation/graph.
- All three expand fns share one internal `runExpansion(nodeId, mutationCall, mapResult)` path: busy-guard (no-op while any expansion in flight), `expandingId` set, functional merge via `mergeGraph(g, added, explorerEdgeKey)` with the **in-flight anchor guard** (discard when `!g.nodes.some(n => n.id === nodeId)`), `expansionTruncated` set inside the guard, `onSettled` clears. Result mapping: `ExpandNetworkNodeOut` → nodes 1:1 / edges mentions; `ExpandSocialNodeOut` → nodes `{id, kind:'author', label, entity_id:null, is_seed:false}` / edges pass through.
- `removeNodes` as in the current hooks (single functional update, incident edges by either endpoint, selection subtraction).
- No ringsRef (rings retired).

- [ ] **Step 1** TDD (~12 tests, mirror `useGraphExplorer.test.tsx` mocking/wrapper): seed from each family; **the payoff test** — seed network (author:auth-1 mentions topic:ent-1), then `expandTies('author:auth-1')` resolving a follows edge to author:auth-2 → ONE canvas where auth-1 has both a mentions and a follows edge, no duplicate auth-1 node; each expand fn calls the right tool with the right args; cross-family edge dedup by key; busy no-op; removed-anchor discard race (deferred promise); removeNodes multi; truncation; error surface. RED → implement → GREEN.
- [ ] **Step 2** Commit `feat(frontend): useUnifiedExplorer — one canvas over both graph families`.

---

### Task 3: ToolExplorer screen + navigation + i18n

**Files:**
- Create: `frontend/src/routes/ToolExplorer.tsx` + `.test.tsx`
- Modify: `frontend/src/routes/Router.tsx`, `frontend/src/layout/Sidebar.tsx`, `frontend/src/i18n/en.ts` + `de.ts`

**Screen structure** (mirror `ToolNetwork.tsx`'s current layout — form → banners → ForceGraph → exports):
- Segmented control (two `Button`-styled radio options or the repo's existing pattern — check `components/form/`) `explorer.seed_entity` | `explorer.seed_author`, state `seedType`.
- Per type, EXACTLY the old screen's fields: entity → EntityInput + depth(1–2)/limit(1–200,25)/topic_limit(1–500,50); author → EntityInput(author label) + depth(1–2)/limit(1–200,25)/second_ring_limit(1–500,50). Submit calls `useToolCall('network_around'|'social_network_around')` and `explorer.seedFromNetwork|seedFromSocial` onSuccess.
- One ForceGraph: `toExplorerForceGraph` memoized; `EXPLORER_NODE_STYLES`/`EXPLORER_EDGE_STYLES`; legend `[seed, author, topic]` from `explorer.legend_*` keys; multiselect/removal wiring as the old screens; **expandActions computed from selection**:

```tsx
const selectedNode = explorer.selectedIds.length === 1
  ? explorer.graph?.nodes.find((n) => n.id === explorer.selectedIds[0]) ?? null : null
const expandActions = selectedNode
  ? selectedNode.kind === 'author'
    ? [ { id: 'topics', label: t('explorer.expand_topics') }, { id: 'ties', label: t('explorer.expand_ties') } ]
    : [ { id: 'mentions', label: t('explorer.expand_mentions') } ]
  : []
const onExpandAction = (actionId: string, nodeId: string) => {
  if (actionId === 'ties') explorer.expandTies(nodeId)
  else if (actionId === 'topics') explorer.expandTopics(nodeId)
  else explorer.expandTopic(nodeId)
}
```
- Exports (JSON/GraphML/HTML) over the unified `fg`, filenames `chorus-explorer.*`, apiRef for HTML — verbatim pattern from the old screens.
- Counts line `explorer.counts` ({n} nodes · {edges} edges); banners: seed truncated (`explorer.capped`), expansion capped (existing `graph.expansion_capped`), expand failed (existing `graph.expand_failed`); empty state `explorer.empty`.
- Router: `/tools/explorer` → ToolExplorer (old two routes REMOVED in Task 5). Sidebar NETWORKS: one item `nav.explorer`.

**i18n adds** (en / de): `explorer.title` 'graph explorer'/'Graph-Explorer'; `explorer.caption` (one sentence: both families, expand-on-click; de translation); `explorer.seed_entity` 'Entity'/'Entität'; `explorer.seed_author` 'Author'/'Autor'; `explorer.expand_topics` 'Expand topics'/'Themen erweitern'; `explorer.expand_ties` 'Expand ties'/'Verbindungen erweitern'; `explorer.expand_mentions` 'Expand mentions'/'Erwähnungen erweitern'; `explorer.build` 'Build graph'/'Graph aufbauen'; `explorer.counts` '{n} node(s) · {edges} edge(s)'/de; `explorer.capped` (reuse old capped wording)/de; `explorer.empty` 'No matches — nothing to draw.'/de; `nav.explorer` 'graph explorer'/'Graph-Explorer'. (Do NOT delete old keys yet — Task 5.)

- [ ] **Step 1** Route tests first (mirror `ToolNetwork.test.tsx` setup, ~9 cases): seed-type switch swaps fields; entity submit calls network_around + renders nodes; author submit calls social_network_around; author selection shows BOTH expand buttons, firing each calls the right tool ('Expand ties' → expand_social_node with stripped author_id); topic selection shows one; cross-family merge grows one canvas; removal; export buttons present post-seed; empty state.
- [ ] **Step 2** Implement screen + router/sidebar + i18n; green; lint/tsc (old screens still present and compiling — full-suite green expected since nothing was deleted yet).
- [ ] **Step 3** Commit `feat(frontend): unified graph explorer screen`.

---

### Task 4: AgentGraphCard on the unified machinery

**Files:**
- Modify: `frontend/src/components/AgentGraphCard.tsx` + `.test.tsx`

Keep: per-tool payload seeding (incl. the expand_* anchor synthesis + shape guard), caption, GRAPH_TRACE_TOOLS, maximize card UX. Change: internals use `useUnifiedExplorer` + `toExplorerForceGraph` + `EXPLORER_*_STYLES`; family dispatch now only decides WHICH seed mapping to call (`seedFromNetwork` for network_around/expand_network_node payloads, `seedFromSocial`-equivalent for social ones — wrap flat expand payloads to the seedable shape exactly as today, social neighbours get is_seed:false); expand affordance becomes the same `expandActions` computation as ToolExplorer (extract that small helper + the action dispatcher into `frontend/src/lib/explorerActions.ts(x)` shared by screen and card, with its own micro-test, rather than duplicating).

- [ ] **Step 1** Adapt the card tests first (existing 8 stay semantically: null-guard, both families render, expand fires the right tool — now via the action buttons, caption, anchor synthesis, malformed shape). RED on internals swap → implement → GREEN.
- [ ] **Step 2** Commit `refactor(frontend): agent graph cards ride the unified explorer machinery`.

---

### Task 5: Deletions, docs, full gate

**Files:**
- Delete: `frontend/src/routes/ToolNetwork.tsx` + `.test.tsx`, `ToolSocial.tsx` + `.test.tsx`, `frontend/src/hooks/useGraphExplorer.ts` + `.test.tsx`, `frontend/src/lib/networkElements.ts` + `.test.ts`, `socialElements.ts` + `.test.ts`
- Modify: `Router.tsx`/`Sidebar.tsx` (old route/nav entries out), `frontend/src/i18n/en.ts`+`de.ts` (dead keys out), `docs/decisions/0016-*.md`, `CLAUDE.md`

- [ ] **Step 1** `git rm` the five module pairs; remove the two old routes + sidebar items.
- [ ] **Step 2** i18n sweep: delete keys used ONLY by the removed screens (the old `network.*`/`social.*` form/count/caption keys and `network.legend_*`/`social.legend_*` — verify each with grep before deleting; keys shared with the agent card or elsewhere stay). Parity test green.
- [ ] **Step 3** Grep gate: `grep -ri "ToolNetwork\|ToolSocial\|useNetworkExplorer\|useSocialExplorer\|networkElements\|socialElements" frontend/src` → empty.
- [ ] **Step 4** ADR 0016 addendum: "Future: unified explorer" → implemented (date, kind-precedence resolution: rings retired, unified kinds seed/author/topic; two screens replaced by `/tools/explorer`; expandActions API v0.5.0; agent cards migrated internally). CLAUDE.md: current-state + "Adding a graph tool" §5 bespoke-graph bullet now name ToolExplorer/useUnifiedExplorer/explorerElements; repo-layout lib/ line updated.
- [ ] **Step 5** Full gate: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build`, then `uv run pytest` and `make verify`. All green.
- [ ] **Step 6** Commits: `refactor(frontend): remove the per-family graph screens and state` + `docs: unified explorer in ADR 0016 + CLAUDE.md`. Controller pushes and opens the PR.
