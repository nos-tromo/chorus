# Reactive Graph Frontend (chorus) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace chorus's one-shot Cytoscape screens with the shared `@infra/ui` `<ForceGraph>` — live physics, selection, expand-on-click via the new backend tools — and render agent graph payloads inline.

**Architecture:** A pure merge helper (`graphExplorer.ts`) plus two explorer hooks own the growing `{nodes, edges}` state and the expand mutations; screens and the agent's inline graph card share them. Mappers translate tool payloads to `ForceGraph` elements; per-kind style maps replace the Cytoscape stylesheets. Cytoscape is removed entirely.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind v4, `@infra/ui#v0.2.0` (ForceGraph), @tanstack/react-query, vitest + happy-dom.

**Repo:** ALL work in `/Users/himarc/dev/nos-tromo/infra/chorus`, branch `feature/reactive-graph` off `main`. **Prerequisites:** infra-ui `v0.2.0` tag exists (plan `2026-07-18-forcegraph-infra-ui.md` merged) AND the backend expand tools are on `main` (plan `2026-07-18-expand-tools-backend.md` merged). Frontend commands run inside `frontend/` with `pnpm`.

## Global Constraints

- Data confidentiality hard rule: test fixtures use fully synthetic invented names/handles only.
- Frontends stay thin HTTP clients — no business logic client-side beyond view state (merge/ring assignment is view state).
- Every user-visible string goes through the i18n catalog (`frontend/src/i18n/en.ts` + `de.ts`); the parity test enforces identical key sets.
- `@infra/ui` is consumed as a pinned git dependency — bump the pin to the `v0.2.0` tag, never a branch.
- `make verify` green before push (pre-commit + `pnpm lint` + `pnpm build`); `git add` new files first.
- After this plan: no `cytoscape` anywhere — dependency, imports, `GraphCanvas.tsx`, `graphStyles.ts` all gone.

---

### Task 1: Dependency bump + API types

**Files:**
- Modify: `frontend/package.json` (`@infra/ui` pin → `v0.2.0`; delete `cytoscape` + `@types/cytoscape`) + `pnpm-lock.yaml` via `pnpm install`
- Modify: `frontend/src/api/types.ts`

**Interfaces:**
- Consumes: backend shapes from the merged backend PR.
- Produces (later tasks import these from `../api/types`):

```ts
export interface ExpandNetworkNodeOut {
  nodes: NetworkNode[]        // existing type (id, kind, label, entity_id, is_seed)
  edges: NetworkEdge[]        // existing type (source, target, weight)
  truncated: boolean
}
export interface SocialNeighbor { id: string; label: string }
export interface ExpandSocialNodeOut {
  nodes: SocialNeighbor[]
  edges: SocialEdge[]         // existing type (source, target, kind, directed)
  truncated: boolean
}
// AgentTraceEntry gains: result: Record<string, unknown> | null
```

- [ ] **Step 1: Bump the pin, drop cytoscape**

In `frontend/package.json`: change the `@infra/ui` git-tag reference from `#v0.1.1` to `#v0.2.0` (read the current line for the exact URL syntax and keep it); delete the `"cytoscape"` and `"@types/cytoscape"` entries. Then:

Run: `cd frontend && pnpm install`
Expected: lockfile updates; `pnpm ls @infra/ui` shows 0.2.0.

- [ ] **Step 2: Add the types**

In `frontend/src/api/types.ts`, next to the existing `NetworkAroundOut`/`SocialNetworkAroundOut` types, add the three interfaces above (reusing the already-declared `NetworkNode`, `NetworkEdge`, `SocialEdge` — read the file for their exact declared names and match them). In `AgentTraceEntry`, add `result: Record<string, unknown> | null`.

- [ ] **Step 3: Typecheck (expected to break where GraphCanvas is imported — that's later tasks' work; confirm ONLY type additions compile)**

Run: `cd frontend && pnpm exec tsc --noEmit 2>&1 | head -30`
Expected: errors mention only `cytoscape`-importing files (`GraphCanvas.tsx`, `graphStyles.ts`, `ToolNetwork.tsx`, `ToolSocial.tsx`, and their tests) — no errors in `api/types.ts`. Those files are rewritten/deleted in Tasks 3–5; the suite is not expected green until Task 5.

- [ ] **Step 4: Commit**

```bash
git checkout -b feature/reactive-graph
git add frontend/package.json frontend/pnpm-lock.yaml frontend/src/api/types.ts
git commit -m "build(frontend): @infra/ui v0.2.0, drop cytoscape; expand-tool types"
```

---

### Task 2: Graph merge helper + explorer hooks

**Files:**
- Create: `frontend/src/lib/graphExplorer.ts`
- Create: `frontend/src/hooks/useGraphExplorer.ts`
- Test: `frontend/src/lib/graphExplorer.test.ts`, `frontend/src/hooks/useGraphExplorer.test.tsx`

**Interfaces:**
- Consumes: `useToolCall` (existing mutation hook), the Task 1 types.
- Produces:

```ts
// lib/graphExplorer.ts — pure
export interface GraphState<N extends { id: string }, E> { nodes: N[]; edges: E[] }
export function mergeGraph<N extends { id: string }, E>(
  current: GraphState<N, E>,
  added: { nodes: N[]; edges: E[] },
  edgeKey: (e: E) => string
): GraphState<N, E>
// nodes dedup by id, FIRST occurrence wins (existing node keeps its ring/seed flags);
// edges dedup by edgeKey, first wins; result arrays are new, inputs untouched.

// hooks/useGraphExplorer.ts
export function useNetworkExplorer(): {
  graph: GraphState<NetworkNode, NetworkEdge> | null
  seedFrom: (out: NetworkAroundOut) => void
  expand: (nodeId: string) => void          // calls expand_network_node
  expandingId: string | null
  expansionTruncated: boolean               // last expansion hit its cap
  selectedId: string | null
  select: (id: string | null) => void
  expandError: string | null
}
export function useSocialExplorer(): same shape over SocialNode/SocialEdge,
  seedFrom(out: SocialNetworkAroundOut), expand() calls expand_social_node with
  author_id = nodeId minus the "author:" prefix, and maps ExpandSocialNodeOut
  neighbours to SocialNode {id, label, ring: ringOf(clicked)+1, is_seed: false}
  before merging.
```

- [ ] **Step 1: Write the failing merge tests**

Create `frontend/src/lib/graphExplorer.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { mergeGraph, type GraphState } from './graphExplorer'

type N = { id: string; ring?: number }
type E = { source: string; target: string; kind?: string }
const key = (e: E) => `${e.source}__${e.target}__${e.kind ?? ''}`

const base: GraphState<N, E> = {
  nodes: [{ id: 'a', ring: 0 }, { id: 'b', ring: 1 }],
  edges: [{ source: 'a', target: 'b', kind: 'follows' }]
}

describe('mergeGraph', () => {
  it('appends new nodes and edges', () => {
    const out = mergeGraph(base, { nodes: [{ id: 'c', ring: 2 }], edges: [{ source: 'b', target: 'c', kind: 'follows' }] }, key)
    expect(out.nodes.map((n) => n.id)).toEqual(['a', 'b', 'c'])
    expect(out.edges).toHaveLength(2)
  })

  it('existing node wins on id collision (keeps its ring)', () => {
    const out = mergeGraph(base, { nodes: [{ id: 'b', ring: 9 }], edges: [] }, key)
    expect(out.nodes.find((n) => n.id === 'b')?.ring).toBe(1)
  })

  it('dedupes edges by key', () => {
    const out = mergeGraph(base, { nodes: [], edges: [{ source: 'a', target: 'b', kind: 'follows' }] }, key)
    expect(out.edges).toHaveLength(1)
  })

  it('same endpoints with different kind are distinct edges', () => {
    const out = mergeGraph(base, { nodes: [], edges: [{ source: 'a', target: 'b', kind: 'friends' }] }, key)
    expect(out.edges).toHaveLength(2)
  })

  it('does not mutate inputs', () => {
    mergeGraph(base, { nodes: [{ id: 'z' }], edges: [] }, key)
    expect(base.nodes).toHaveLength(2)
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm test -- src/lib/graphExplorer.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the merge helper**

Create `frontend/src/lib/graphExplorer.ts`:

```ts
/**
 * Pure merge for the growing explorer graph. Nodes dedup by id with the
 * existing node winning (it carries authoritative ring/seed flags); edges
 * dedup by a caller-supplied key so parallel edges of different kinds survive.
 */
export interface GraphState<N extends { id: string }, E> {
  nodes: N[]
  edges: E[]
}

export function mergeGraph<N extends { id: string }, E>(
  current: GraphState<N, E>,
  added: { nodes: N[]; edges: E[] },
  edgeKey: (e: E) => string
): GraphState<N, E> {
  const seenNodes = new Set(current.nodes.map((n) => n.id))
  const nodes = [...current.nodes]
  for (const n of added.nodes) {
    if (seenNodes.has(n.id)) continue
    seenNodes.add(n.id)
    nodes.push(n)
  }
  const seenEdges = new Set(current.edges.map(edgeKey))
  const edges = [...current.edges]
  for (const e of added.edges) {
    const k = edgeKey(e)
    if (seenEdges.has(k)) continue
    seenEdges.add(k)
    edges.push(e)
  }
  return { nodes, edges }
}
```

- [ ] **Step 4: Run merge tests to green, commit**

Run: `cd frontend && pnpm test -- src/lib/graphExplorer.test.ts` → PASS (5).

```bash
git add frontend/src/lib/graphExplorer.ts frontend/src/lib/graphExplorer.test.ts
git commit -m "feat(frontend): pure graph merge for the explorer state"
```

- [ ] **Step 5: Write the failing hook tests**

Create `frontend/src/hooks/useGraphExplorer.test.tsx`. Mock `../api/tools`' `callTool` with `vi.mock` (the pattern other hook tests in this repo use — read one first, e.g. `useToolCall`'s consumers). Wrap in the repo's QueryClient test wrapper. Cases:

```tsx
// useNetworkExplorer
it('seedFrom replaces the graph and clears selection', ...)
  // seedFrom(fixture NetworkAroundOut with seed topic:ent-1 + author:auth-1)
  // -> graph has 2 nodes; select('author:auth-1'); seedFrom(again) -> selectedId null

it('expand merges neighbour payload and tracks expandingId', async ...)
  // callTool resolves ExpandNetworkNodeOut{nodes:[topic:ent-2], edges:[auth-1->ent-2], truncated:false}
  // expand('author:auth-1') -> expandingId==='author:auth-1' while pending;
  // after resolve: graph has 3 nodes, expandingId null

it('expansionTruncated reflects the last expansion', async ...)
  // resolve with truncated:true -> expansionTruncated true; next expansion false resets it

// useSocialExplorer
it('expand strips the author: prefix and assigns ring+1', async ...)
  // seedFrom(SocialNetworkAroundOut with seed ring0 author:auth-a, ring1 author:auth-b)
  // expand('author:auth-b'); assert callTool called with
  // ('expand_social_node', { author_id: 'auth-b', limit: 50 });
  // resolved neighbour author:auth-c gets ring 2, is_seed false

it('expand surfaces an error message on failure', async ...)
  // callTool rejects -> expandError is the message, expandingId null
```

Write these as complete tests against the fixtures above (all ids synthetic).

- [ ] **Step 6: Implement the hooks**

Create `frontend/src/hooks/useGraphExplorer.ts`:

```ts
/**
 * Explorer state for the two graph screens and the agent's inline graphs:
 * a growing {nodes, edges} graph seeded from a seed-tool payload and grown by
 * the expand-on-click tools, plus selection. Merge/ring assignment is view
 * state — the backend returns flat neighbour lists.
 */
import { useCallback, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { callTool } from '../api/tools'
import { mergeGraph, type GraphState } from '../lib/graphExplorer'
import type {
  ExpandNetworkNodeOut,
  ExpandSocialNodeOut,
  NetworkAroundOut,
  NetworkEdge,
  NetworkNode,
  SocialEdge,
  SocialNetworkAroundOut,
  SocialNode
} from '../api/types'

const EXPAND_LIMIT = 50

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export function useNetworkExplorer() {
  const [graph, setGraph] = useState<GraphState<NetworkNode, NetworkEdge> | null>(null)
  const [selectedId, select] = useState<string | null>(null)
  const [expandingId, setExpandingId] = useState<string | null>(null)
  const [expansionTruncated, setExpansionTruncated] = useState(false)

  const mutation = useMutation({
    mutationFn: (nodeId: string) =>
      callTool<ExpandNetworkNodeOut>('expand_network_node', { node_id: nodeId, limit: EXPAND_LIMIT })
  })

  const seedFrom = useCallback((out: NetworkAroundOut) => {
    setGraph({ nodes: out.nodes, edges: out.edges })
    select(null)
    setExpansionTruncated(false)
  }, [])

  const expand = useCallback(
    (nodeId: string) => {
      setExpandingId(nodeId)
      mutation.mutate(nodeId, {
        onSuccess: (out) => {
          setGraph((g) =>
            g ? mergeGraph(g, out, (e) => `${e.source}__${e.target}`) : g
          )
          setExpansionTruncated(out.truncated)
        },
        onSettled: () => setExpandingId(null)
      })
    },
    [mutation]
  )

  return {
    graph,
    seedFrom,
    expand,
    expandingId,
    expansionTruncated,
    selectedId,
    select,
    expandError: mutation.isError ? errText(mutation.error) : null
  }
}

export function useSocialExplorer() {
  const [graph, setGraph] = useState<GraphState<SocialNode, SocialEdge> | null>(null)
  const [selectedId, select] = useState<string | null>(null)
  const [expandingId, setExpandingId] = useState<string | null>(null)
  const [expansionTruncated, setExpansionTruncated] = useState(false)
  // Ring lookup for ring+1 assignment on expansion; refreshed on every graph set.
  const ringsRef = useRef<Map<string, number>>(new Map())

  const remember = (nodes: SocialNode[]) => {
    for (const n of nodes) ringsRef.current.set(n.id, n.ring)
  }

  const mutation = useMutation({
    mutationFn: (nodeId: string) =>
      callTool<ExpandSocialNodeOut>('expand_social_node', {
        author_id: nodeId.replace(/^author:/, ''),
        limit: EXPAND_LIMIT
      })
  })

  const seedFrom = useCallback((out: SocialNetworkAroundOut) => {
    ringsRef.current = new Map()
    remember(out.nodes)
    setGraph({ nodes: out.nodes, edges: out.edges })
    select(null)
    setExpansionTruncated(false)
  }, [])

  const expand = useCallback(
    (nodeId: string) => {
      setExpandingId(nodeId)
      mutation.mutate(nodeId, {
        onSuccess: (out) => {
          const ring = (ringsRef.current.get(nodeId) ?? 0) + 1
          const added = {
            nodes: out.nodes.map((n) => ({ id: n.id, label: n.label, ring, is_seed: false })),
            edges: out.edges
          }
          remember(added.nodes)
          setGraph((g) =>
            g ? mergeGraph(g, added, (e) => `${e.source}__${e.target}__${e.kind}`) : g
          )
          setExpansionTruncated(out.truncated)
        },
        onSettled: () => setExpandingId(null)
      })
    },
    [mutation]
  )

  return {
    graph,
    seedFrom,
    expand,
    expandingId,
    expansionTruncated,
    selectedId,
    select,
    expandError: mutation.isError ? errText(mutation.error) : null
  }
}
```

(Adjust the `callTool` import path/signature to the actual `frontend/src/api/tools.ts` export — read it first.)

- [ ] **Step 7: Run hook tests to green, commit**

Run: `cd frontend && pnpm test -- src/hooks/useGraphExplorer.test.tsx` → PASS (6).

```bash
git add frontend/src/hooks/useGraphExplorer.ts frontend/src/hooks/useGraphExplorer.test.tsx
git commit -m "feat(frontend): network/social explorer hooks with expand-on-click merge"
```

---

### Task 3: ForceGraph mappers + style maps (replace Cytoscape mappers)

**Files:**
- Rewrite: `frontend/src/lib/networkElements.ts` + `.test.ts`
- Rewrite: `frontend/src/lib/socialElements.ts` + `.test.ts`
- Delete: `frontend/src/lib/graphStyles.ts` (its palette moves into the style maps below)

**Interfaces:**
- Consumes: `GraphState` from `./graphExplorer`; `ForceGraphNode`, `ForceGraphEdge`, `ForceGraphNodeStyle`, `ForceGraphEdgeStyle` types from `@infra/ui`.
- Produces:

```ts
// networkElements.ts
export const NETWORK_NODE_STYLES: Record<string, ForceGraphNodeStyle>
  // { seed: {color:'#fbbf24'}, author: {color:'#7c3aed'}, topic: {color:'#4ade80'} }
export function toNetworkForceGraph(g: GraphState<NetworkNode, NetworkEdge>): {
  nodes: ForceGraphNode[]; edges: ForceGraphEdge[]
}
  // node.kind = n.is_seed ? 'seed' : n.kind; node.size = 1 + total incident edge
  // weight (seed floor 6); edge = {source, target, kind:'mentions', weight}
// socialElements.ts
export const SOCIAL_NODE_STYLES: Record<string, ForceGraphNodeStyle>
  // { seed:'#fbbf24', ring1:'#7c3aed', ring2:'#64748b', ringN:'#b0bec5' }
export const SOCIAL_EDGE_STYLES: Record<string, ForceGraphEdgeStyle>
  // { follows: {opacity:0.7}, friends: {dashed:true} }
export function toSocialForceGraph(g: GraphState<SocialNode, SocialEdge>): {
  nodes: ForceGraphNode[]; edges: ForceGraphEdge[]
}
  // node.kind = is_seed ? 'seed' : ring===1 ? 'ring1' : ring===2 ? 'ring2' : 'ringN'
  // node.size: seed 6, ring1 3, ring2 2, ringN 1
  // edge = {source, target, kind, directed} (directed true only for follows)
```

- [ ] **Step 1: Rewrite the tests first** — replace each existing `.test.ts` wholesale with tests of the new pure functions: kind derivation (seed/author/topic; ring classes), size rules (seed floor, incident-weight sum), edge kind/directed/weight passthrough, and determinism (same input → deep-equal output). Port the spirit of the old dedup tests to note dedup now lives in `mergeGraph` (no dedup in mappers — inputs are already deduped state). ~8 tests per file, synthetic fixtures.

- [ ] **Step 2: Run to verify failure** (`pnpm test -- src/lib/networkElements src/lib/socialElements` — old implementations don't export the new names).

- [ ] **Step 3: Rewrite both mappers** to the produced-interface contract above. They are small pure functions (~40 lines each); no Cytoscape imports remain. Delete `frontend/src/lib/graphStyles.ts`.

- [ ] **Step 4: Run to green**: `pnpm test -- src/lib` → PASS.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src/lib
git commit -m "feat(frontend): ForceGraph mappers + style maps replace Cytoscape stylesheets"
```

---### Task 4: i18n keys

**Files:**
- Modify: `frontend/src/i18n/en.ts`, `frontend/src/i18n/de.ts`

**Interfaces:**
- Produces the keys Tasks 5–6 call via `t(...)`. Add to BOTH catalogs (parity test enforces):

```ts
// en.ts — insert as a "// graph explorer (shared)" block
'graph.min_edges': 'Min edges',
'graph.edge_length': 'Edge length',
'graph.zoom': 'Zoom',
'graph.reset': 'Reset',
'graph.expand_node': 'Expand node',
'graph.maximize': 'Expand graph',
'graph.minimize': 'Collapse graph',
'graph.expansion_capped': 'Expansion capped — the most connected {limit} neighbors are shown.',
'graph.expand_failed': 'Expansion failed: {error}',
'graph.hint': 'Scroll to zoom, drag to move, click to select, double-click to expand.',
'network.legend_seed': 'Seed',
'network.legend_author': 'Authors',
'network.legend_topic': 'Topics',
'social.legend_seed': 'Seed',
'social.legend_ring1': 'Direct ties',
'social.legend_ring2': 'Second ring',
'social.legend_ringN': 'Further out',
'agent.graph_result': 'Graph from {tool}',
```

German (`de.ts`), same keys: `'Min. Kanten'`, `'Kantenlänge'`, `'Zoom'`, `'Zurücksetzen'`, `'Knoten erweitern'`, `'Graph vergrößern'`, `'Graph verkleinern'`, `'Erweiterung begrenzt — die {limit} am stärksten vernetzten Nachbarn werden angezeigt.'`, `'Erweiterung fehlgeschlagen: {error}'`, `'Scrollen zum Zoomen, Ziehen zum Verschieben, Klick zum Auswählen, Doppelklick zum Erweitern.'`, `'Ausgangspunkt'`, `'Autoren'`, `'Themen'`, `'Ausgangspunkt'`, `'Direkte Verbindungen'`, `'Zweiter Ring'`, `'Weiter entfernt'`, `'Graph aus {tool}'`.

- [ ] **Step 1: Add the keys to both files** (respect each file's comment-block style and key ordering).
- [ ] **Step 2: Run the parity test**: `pnpm test -- src/i18n` → PASS.
- [ ] **Step 3: Commit**: `git add frontend/src/i18n && git commit -m "feat(frontend): i18n keys for the graph explorer"`

---

### Task 5: Rewrite the two graph screens

**Files:**
- Rewrite: `frontend/src/routes/ToolNetwork.tsx`, `frontend/src/routes/ToolSocial.tsx`
- Create: `frontend/src/routes/ToolNetwork.test.tsx`, `frontend/src/routes/ToolSocial.test.tsx` (closing the existing route-test gap)
- Delete: `frontend/src/components/GraphCanvas.tsx`, `frontend/src/components/GraphCanvas.test.tsx`

**Interfaces:**
- Consumes: `useToolCall` (seed query), `useNetworkExplorer`/`useSocialExplorer` (Task 2), mappers + style maps (Task 3), `ForceGraph` from `@infra/ui`, i18n keys (Task 4).
- Produces: same routes/paths as today (`Router.tsx` and `Sidebar.tsx` need no changes).

Structure for BOTH screens (ToolSocial shown; ToolNetwork is identical modulo its form fields, mapper, styles, legend keys, and `seed`-tool name — keep each screen's existing form JSX):

```tsx
export function ToolSocial() {
  const t = useT()
  const mutation = useToolCall<SocialNetworkAroundOut>('social_network_around')
  const explorer = useSocialExplorer()
  // ... existing form state unchanged ...

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    mutation.mutate(
      { author, depth, limit, second_ring_limit: secondRingLimit },
      { onSuccess: (out) => explorer.seedFrom(out) }
    )
  }

  const fg = useMemo(
    () => (explorer.graph ? toSocialForceGraph(explorer.graph) : null),
    [explorer.graph]
  )

  return (
    <div className="p-8 space-y-6">
      {/* title + form + error/loading blocks: unchanged from the current file */}
      {fg && explorer.graph && (
        explorer.graph.nodes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('social.empty')}</p>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {t('social.counts', { n: explorer.graph.nodes.length, edges: explorer.graph.edges.length,
                follows: followsCount, friends: friendsCount })}
            </p>
            {mutation.data?.truncated && <Banner variant="info">{t('social.capped')}</Banner>}
            {explorer.expansionTruncated && (
              <Banner variant="info">{t('graph.expansion_capped', { limit: 50 })}</Banner>
            )}
            {explorer.expandError && (
              <Banner variant="danger">{t('graph.expand_failed', { error: explorer.expandError })}</Banner>
            )}
            <ForceGraph
              nodes={fg.nodes}
              edges={fg.edges}
              nodeStyles={SOCIAL_NODE_STYLES}
              edgeStyles={SOCIAL_EDGE_STYLES}
              selectedId={explorer.selectedId}
              onSelectNode={explorer.select}
              onExpandNode={explorer.expand}
              expandingId={explorer.expandingId}
              statusText={t('graph.hint')}
              legend={[
                { kind: 'seed', label: t('social.legend_seed') },
                { kind: 'ring1', label: t('social.legend_ring1') },
                { kind: 'ring2', label: t('social.legend_ring2') },
                { kind: 'ringN', label: t('social.legend_ringN') }
              ]}
              labels={{
                minEdges: t('graph.min_edges'), edgeLength: t('graph.edge_length'),
                zoom: t('graph.zoom'), reset: t('graph.reset'),
                expandSelected: t('graph.expand_node'),
                maximize: t('graph.maximize'), minimize: t('graph.minimize')
              }}
            />
          </div>
        )
      )}
    </div>
  )
}
```

`ToolNetwork` differences: seed tool `network_around` (payload `{entity, depth, limit, topic_limit}` — keep the existing form), `useNetworkExplorer`, `toNetworkForceGraph`, `NETWORK_NODE_STYLES` (no edge styles), legend from `network.legend_*`, counts line from the screen's existing keys.

- [ ] **Step 1: Write the failing route tests.** For each screen (mock `../api/tools`'s `callTool`; render with the repo's QueryClient + ConfigProvider test wrappers — mirror `ToolAuthorActivity.test.tsx`'s setup):
  1. submit form → seed payload renders `<ForceGraph>` with one `g[role="button"]` per node
  2. seed `truncated: true` → info banner
  3. click node then the "Expand node" button → `callTool` called with the expand tool + correct args; merged neighbour appears (node count grows)
  4. expansion `truncated: true` → `graph.expansion_capped` banner
  5. expand rejection → `graph.expand_failed` banner
  6. empty seed result → empty-state text, no svg

- [ ] **Step 2: Run to verify failure**, then rewrite both screens per the structure above.

- [ ] **Step 3: Delete GraphCanvas**

```bash
git rm frontend/src/components/GraphCanvas.tsx frontend/src/components/GraphCanvas.test.tsx
```

- [ ] **Step 4: Full frontend gate**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build`
Expected: all green; `grep -ri cytoscape frontend/src frontend/package.json` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src frontend/package.json
git commit -m "feat(frontend): reactive explorer screens on ForceGraph; remove Cytoscape"
```

---

### Task 6: Inline graphs in the agent screen

**Files:**
- Create: `frontend/src/components/AgentGraphCard.tsx` + `.test.tsx`
- Modify: `frontend/src/routes/Agent.tsx` (render cards under each assistant answer)

**Interfaces:**
- Consumes: `AgentTraceEntry.result` (Task 1 type), both explorer hooks, both mappers, `ForceGraph`.
- Produces: `AgentGraphCard({ entry }: { entry: AgentTraceEntry })` — renders `null` unless `entry.result` is non-null and `entry.tool` is one of the four graph tools.

Behavior: choose family by tool name — `network_around`/`expand_network_node` → network mapper/styles/`useNetworkExplorer`; `social_network_around`/`expand_social_node` → social. On mount (`useEffect` keyed on `entry`), `seedFrom(entry.result as ...Out)`; for the two `expand_*` tools wrap the flat payload into a seedable shape (`{seed:'', seed_node_id:null, nodes, edges, truncated}` — for the social case assign `ring: 1, is_seed: false` to each neighbour). Render a captioned card (`t('agent.graph_result', { tool: entry.tool })`) with the same `<ForceGraph …>` wiring as the screens — expansion works inside the card via the hook, so an agent answer is a live, expandable graph.

In `Agent.tsx`, where an assistant turn's trace entries render (next to the existing `<ToolTrace>`), add:

```tsx
{trace.filter((s) => s.result != null).map((s, i) => (
  <AgentGraphCard key={`${s.tool}:${i}`} entry={s} />
))}
```

(read `Agent.tsx` first for the actual trace variable/loop shape and match it; `ToolTrace` itself stays unchanged).

- [ ] **Step 1: Write failing AgentGraphCard tests** — renders null without `result`; renders a ForceGraph with the right node count for a `network_around` payload; renders for a `social_network_around` payload with ring styling kinds; expand button calls the right expand tool (mock `callTool`); caption shows the tool name.
- [ ] **Step 2: Implement the card + Agent.tsx wiring; run to green** (`pnpm test -- src/components/AgentGraphCard src/routes/Agent`).
- [ ] **Step 3: Commit**: `git add frontend/src/components/AgentGraphCard.* frontend/src/routes/Agent.tsx && git commit -m "feat(frontend): inline expandable graphs in agent answers"`

---

### Task 7: ADR + full verification + PR

**Files:**
- Create: `docs/decisions/` next-numbered ADR (check `ls docs/decisions/` for the next number, e.g. `0016-forcegraph-and-incremental-expansion.md`)

- [ ] **Step 1: Write the ADR** — short record: context (one-shot Cytoscape, no exploration), decision (shared `@infra/ui` ForceGraph port of docint's engine; two audited expand tools; agent trace payloads wiring (a); docint migration deferred), alternatives considered (keep Cytoscape + plugins; per-app copies; frontend replay wiring (b)), consequences (cytoscape removed; depth cap stays; §76 row per expansion). Link the spec: `docs/superpowers/specs/2026-07-18-reactive-graph-exploration-design.md`.

Include a short **Future: unified explorer** paragraph recording that merging the two graph screens into one combined explorer was considered and deliberately deferred, and why it stays additive: the renderer (`ForceGraph`) is family-agnostic; both families share the namespaced `author:<id>` node id space so a merged canvas dedupes authors for free via `mergeGraph`; the expand tools are node-scoped (an author node can offer both "topics" and "ties" expansions with zero backend change); and edge kinds (`mentions` vs `follows`/`friends`) don't collide, so style maps concatenate. What a future merge adds fresh (no rewrites): a union node view-model plus a kind/color precedence policy for nodes that are both social neighbors and mentioning authors, one combined screen/hook, and a dual-expand affordance — the per-family hooks get superseded, the pure pieces carry over.

- [ ] **Step 2: Update CLAUDE.md touchpoints** — in `CLAUDE.md`: the "Adding a graph tool" §5 bespoke-graph bullet now names `ForceGraph`/`useGraphExplorer` instead of Cytoscape element mappers; the Tech-stack "Graph visualization" line becomes the shared `@infra/ui` ForceGraph (SVG force sim, no Cytoscape). Grep for `cytoscape`/`Cytoscape` in `CLAUDE.md`, `README.md`, `docs/` and update the stale mentions.

- [ ] **Step 3: Full verification**

Run: `uv run pytest && make verify`
Expected: green (backend suite unaffected but run it — the conftest list changed in the backend PR this builds on).

- [ ] **Step 4: PR**

```bash
git push -u origin feature/reactive-graph
gh pr create --title "feat: reactive graph exploration (ForceGraph + expand-on-click + agent inline graphs)" --body "Frontend half of docs/superpowers/specs/2026-07-18-reactive-graph-exploration-design.md. Adopts @infra/ui v0.2.0 ForceGraph, adds explorer state with expand-on-click via the new backend tools, renders agent graph payloads inline, removes Cytoscape. ADR included."
```
