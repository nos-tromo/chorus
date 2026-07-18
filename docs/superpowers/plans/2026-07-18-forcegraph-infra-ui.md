# ForceGraph Primitive (infra-ui) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a reusable, dependency-free `<ForceGraph>` React primitive in `@infra/ui` (force-simulation SVG graph with zoom/pan/drag/select/expand and incremental merge), released as `v0.3.0`.

**Architecture:** Port docint's proven `forceGraph.ts` simulation verbatim, add a pure position-merge helper, and generalize docint's `EntityGraph.tsx` into an app-agnostic component whose node/edge styling comes from consumer props. Interior chrome (edges, labels, selection ring) binds to Tailwind token classes so it follows dark/light themes; node fills stay consumer-supplied.

**Tech Stack:** React 19, TypeScript, Tailwind v4 (token classes), tsup build, vitest + happy-dom. No new runtime dependencies.

**Repo:** ALL work in this plan happens in `/Users/himarc/dev/nos-tromo/infra/infra-ui` (its own git repo — branch, commit, and PR there, never at the `infra/` root). Reference sources are read from `/Users/himarc/dev/nos-tromo/infra/docint` (read-only — never modify docint).

## Global Constraints

- No new runtime dependencies in `package.json` (`dependencies` stays exactly `class-variance-authority`, `clsx`, `tailwind-merge`).
- No `Math.random()` / `Date.now()` in the simulation or component — determinism is a documented property of the ported engine.
- `dist/` is committed: after any `src/` change compiles, run `pnpm build` and commit the regenerated `dist/` in the same commit (infra-ui's committed-`dist/` rule).
- All quality gates must pass before the release commit: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`.
- Version bumps to `0.3.0` in `package.json`; the release tag is minted by the shared release-tag workflow on merge (do not hand-cut a tag).
- Node fill colors are consumer props; everything else (edge stroke, label fill, selection ring, control chrome) uses Tailwind token classes (`stroke-border`, `fill-muted-foreground`, `text-muted-foreground`, `border-border`, …) so the component is theme-aware.

---

### Task 1: Port the force simulation

**Files:**
- Create: `src/graph/forceGraph.ts` (copy of docint's file)
- Test: `src/graph/forceGraph.test.ts`

**Interfaces:**
- Consumes: nothing (self-contained module).
- Produces: `createForceSimulation(nodes: ForceNode[], links: ForceLink[], options?: Partial<SimulationOptions>): ForceSimulation`, `phyllotaxisSeed(count: number, centerX: number, centerY: number, spacing?: number): Array<{x: number; y: number}>`, and the types `ForceNode {id, x, y, vx, vy, r, fx?, fy?}`, `ForceLink {source, target, weight}`, `SimulationOptions`, `ForceSimulation` — all exactly as in the source file. Later tasks import from `'./forceGraph'`.

- [ ] **Step 1: Copy the file verbatim**

```bash
mkdir -p src/graph
cp /Users/himarc/dev/nos-tromo/infra/docint/frontend/src/lib/forceGraph.ts src/graph/forceGraph.ts
```

Then edit only the opening doc comment's first paragraph (it says "docint ships no graph library") to:

```ts
/**
 * A tiny, dependency-free force-directed graph simulation.
 *
 * @infra/ui ships no graph library (airgap-safe, minimal deps), so ForceGraph
 * runs this self-contained layout instead. It is intentionally a small
 * O(n²) solver — fine for the ≤ a few-hundred nodes these graphs ever
 * show — modelled on d3-force's structure (velocity-Verlet integration with
 * many-body repulsion, link springs, a gentle centering pull and pairwise
 * collision). The simulation is deterministic given its inputs (callers seed
 * positions, never `Math.random`), which keeps it unit-testable.
 */
```

No other changes — the code body ports as-is.

- [ ] **Step 2: Write the failing test**

Create `src/graph/forceGraph.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { createForceSimulation, phyllotaxisSeed, type ForceNode } from './forceGraph'

function mkNodes(n: number): ForceNode[] {
  const seeds = phyllotaxisSeed(n, 0, 0)
  return seeds.map((s, i) => ({ id: `n${i}`, x: s.x, y: s.y, vx: 0, vy: 0, r: 10 }))
}

describe('phyllotaxisSeed', () => {
  it('generates the requested count, deterministically', () => {
    const a = phyllotaxisSeed(5, 100, 50)
    const b = phyllotaxisSeed(5, 100, 50)
    expect(a).toHaveLength(5)
    expect(a).toEqual(b)
  })

  it('spreads points apart (no two coincide)', () => {
    const pts = phyllotaxisSeed(20, 0, 0)
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++)
        expect(Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y)).toBeGreaterThan(1)
  })
})

describe('createForceSimulation', () => {
  it('settles below alphaMin after enough ticks', () => {
    const sim = createForceSimulation(mkNodes(6), [
      { source: 'n0', target: 'n1', weight: 1 },
      { source: 'n1', target: 'n2', weight: 1 }
    ])
    for (let i = 0; i < 500 && !sim.isSettled(); i++) sim.tick()
    expect(sim.isSettled()).toBe(true)
  })

  it('is deterministic: same inputs, same final positions', () => {
    const run = () => {
      const sim = createForceSimulation(mkNodes(8), [{ source: 'n0', target: 'n7', weight: 2 }])
      for (let i = 0; i < 300; i++) sim.tick()
      return sim.nodes.map((n) => [n.x, n.y])
    }
    expect(run()).toEqual(run())
  })

  it('pins a fixed node and releases it', () => {
    const sim = createForceSimulation(mkNodes(4), [])
    sim.fixNode('n2', 123, 456)
    sim.tick()
    const n2 = sim.nodeById('n2')!
    expect(n2.x).toBe(123)
    expect(n2.y).toBe(456)
    sim.releaseNode('n2')
    expect(n2.fx).toBeNull()
    expect(n2.fy).toBeNull()
  })

  it('reheat raises alpha so a settled sim moves again', () => {
    const sim = createForceSimulation(mkNodes(3), [])
    for (let i = 0; i < 500; i++) sim.tick()
    expect(sim.isSettled()).toBe(true)
    sim.reheat()
    expect(sim.isSettled()).toBe(false)
  })

  it('setOptions changes live behavior (wider linkDistance spreads endpoints)', () => {
    const mk = () =>
      createForceSimulation(mkNodes(2), [{ source: 'n0', target: 'n1', weight: 1 }])
    const near = mk()
    for (let i = 0; i < 400; i++) near.tick()
    const far = mk()
    far.setOptions({ linkDistance: 300 })
    for (let i = 0; i < 400; i++) far.tick()
    const dist = (s: ReturnType<typeof mk>) => {
      const a = s.nodeById('n0')!
      const b = s.nodeById('n1')!
      return Math.hypot(a.x - b.x, a.y - b.y)
    }
    expect(dist(far)).toBeGreaterThan(dist(near))
  })

  it('drops links whose endpoints are missing or self-referential', () => {
    const sim = createForceSimulation(mkNodes(2), [
      { source: 'n0', target: 'ghost', weight: 1 },
      { source: 'n1', target: 'n1', weight: 1 }
    ])
    // Must not throw while ticking with the bogus links filtered out.
    for (let i = 0; i < 50; i++) sim.tick()
    expect(sim.nodes).toHaveLength(2)
  })
})
```

- [ ] **Step 3: Run test to verify it passes** (the implementation was copied first, so these pass immediately — the test's job here is to lock in the ported behavior)

Run: `pnpm test -- src/graph/forceGraph.test.ts`
Expected: PASS (all 8 tests)

- [ ] **Step 4: Lint + typecheck**

Run: `pnpm lint && pnpm typecheck`
Expected: clean. Fix any lint style deltas (docint uses a different prettier config; run `pnpm format` if needed).

- [ ] **Step 5: Commit**

```bash
git checkout -b feature/forcegraph
git add src/graph/forceGraph.ts src/graph/forceGraph.test.ts
git commit -m "feat: port dependency-free force simulation from docint"
```

---

### Task 2: Position-merge helper for incremental graphs

**Files:**
- Create: `src/graph/mergePositions.ts`
- Test: `src/graph/mergePositions.test.ts`

**Interfaces:**
- Consumes: `phyllotaxisSeed` from `./forceGraph`.
- Produces: `seedPositions(nodes: Array<{id: string}>, edges: Array<{source: string; target: string}>, previous: ReadonlyMap<string, {x: number; y: number}>, centerX: number, centerY: number): Map<string, {x: number; y: number}>` — Task 3's component calls this whenever the node set changes.

Behavior contract (this is the "incremental merge" from the spec):
1. A node whose id is in `previous` keeps its previous position exactly.
2. A new node with at least one neighbor (via `edges`) already in `previous` seeds AT that neighbor's position plus a small deterministic offset (index-derived angle, radius 30) — so expansions bloom out of the clicked node.
3. A new node with no previously-placed neighbor takes the next free phyllotaxis slot around the center.

- [ ] **Step 1: Write the failing test**

Create `src/graph/mergePositions.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { seedPositions } from './mergePositions'

const CX = 480
const CY = 310

describe('seedPositions', () => {
  it('keeps previous positions for existing ids', () => {
    const prev = new Map([['a', { x: 10, y: 20 }]])
    const out = seedPositions([{ id: 'a' }], [], prev, CX, CY)
    expect(out.get('a')).toEqual({ x: 10, y: 20 })
  })

  it('seeds a new node near an already-placed neighbor', () => {
    const prev = new Map([['a', { x: 100, y: 100 }]])
    const out = seedPositions(
      [{ id: 'a' }, { id: 'b' }],
      [{ source: 'a', target: 'b' }],
      prev,
      CX,
      CY
    )
    const b = out.get('b')!
    expect(Math.hypot(b.x - 100, b.y - 100)).toBeLessThanOrEqual(30.001)
    expect(Math.hypot(b.x - 100, b.y - 100)).toBeGreaterThan(0)
  })

  it('two new neighbors of the same anchor get distinct positions', () => {
    const prev = new Map([['a', { x: 0, y: 0 }]])
    const out = seedPositions(
      [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
      [
        { source: 'a', target: 'b' },
        { source: 'a', target: 'c' }
      ],
      prev,
      CX,
      CY
    )
    expect(out.get('b')).not.toEqual(out.get('c'))
  })

  it('falls back to phyllotaxis for unconnected new nodes', () => {
    const out = seedPositions([{ id: 'x' }, { id: 'y' }], [], new Map(), CX, CY)
    expect(out.get('x')).toBeDefined()
    expect(out.get('y')).toBeDefined()
    expect(out.get('x')).not.toEqual(out.get('y'))
  })

  it('is deterministic', () => {
    const args = [
      [{ id: 'a' }, { id: 'b' }],
      [{ source: 'a', target: 'b' }],
      new Map([['a', { x: 5, y: 5 }]]),
      CX,
      CY
    ] as const
    expect(seedPositions(...args)).toEqual(seedPositions(...args))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test -- src/graph/mergePositions.test.ts`
Expected: FAIL — cannot resolve `./mergePositions`.

- [ ] **Step 3: Write the implementation**

Create `src/graph/mergePositions.ts`:

```ts
/**
 * Seed layout positions for a (possibly grown) node set.
 *
 * Existing nodes keep their previous positions so an incremental expansion
 * never re-scrambles the layout the user is looking at; new nodes bloom out
 * of an already-placed neighbor (deterministic index-derived angle) or, when
 * unconnected, take fresh phyllotaxis slots around the center.
 */
import { phyllotaxisSeed } from './forceGraph'

const BLOOM_RADIUS = 30
const GOLDEN = Math.PI * (3 - Math.sqrt(5))

export function seedPositions(
  nodes: Array<{ id: string }>,
  edges: Array<{ source: string; target: string }>,
  previous: ReadonlyMap<string, { x: number; y: number }>,
  centerX: number,
  centerY: number
): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>()
  const neighborOf = new Map<string, string>()
  for (const e of edges) {
    if (previous.has(e.source) && !previous.has(e.target) && !neighborOf.has(e.target))
      neighborOf.set(e.target, e.source)
    if (previous.has(e.target) && !previous.has(e.source) && !neighborOf.has(e.source))
      neighborOf.set(e.source, e.target)
  }

  const orphans: string[] = []
  let bloomIndex = 0
  for (const n of nodes) {
    const prev = previous.get(n.id)
    if (prev) {
      out.set(n.id, { x: prev.x, y: prev.y })
      continue
    }
    const anchorId = neighborOf.get(n.id)
    const anchor = anchorId ? previous.get(anchorId) : undefined
    if (anchor) {
      const theta = bloomIndex * GOLDEN
      bloomIndex += 1
      out.set(n.id, {
        x: anchor.x + BLOOM_RADIUS * Math.cos(theta),
        y: anchor.y + BLOOM_RADIUS * Math.sin(theta)
      })
      continue
    }
    orphans.push(n.id)
  }

  // Unconnected newcomers: fresh spiral slots offset past the already-used
  // count so they do not stack onto slots earlier nodes may occupy. Capture
  // the offset BEFORE assigning — out.size mutates inside the loop.
  const base = out.size
  const spiral = phyllotaxisSeed(base + orphans.length, centerX, centerY, 30)
  orphans.forEach((id, i) => out.set(id, spiral[base + i]))
  return out
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test -- src/graph/mergePositions.test.ts`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/graph/mergePositions.ts src/graph/mergePositions.test.ts
git commit -m "feat: position-merge seeding for incremental graph growth"
```

---

### Task 3: The `<ForceGraph>` component

**Files:**
- Create: `src/graph/ForceGraph.tsx`
- Test: `src/graph/ForceGraph.test.tsx`
- Reference (read, do not modify): `/Users/himarc/dev/nos-tromo/infra/docint/frontend/src/components/analysis/EntityGraph.tsx`

**Interfaces:**
- Consumes: `createForceSimulation`, `ForceNode`, `ForceLink` from `./forceGraph`; `seedPositions` from `./mergePositions`; `cn` from `../cn`.
- Produces (the public API later tasks and consumer apps rely on — copy exactly):

```ts
export interface ForceGraphNode {
  id: string
  label: string
  /** Style-map key; also shown in the legend. */
  kind: string
  /** Relative size weight (≥1); mapped to radius by sqrt scale, like docint's mentions. */
  size?: number
}

export interface ForceGraphEdge {
  source: string
  target: string
  kind: string
  /** Draw an arrowhead source → target. */
  directed?: boolean
  /** Stroke-width weight (≥1). */
  weight?: number
}

export interface ForceGraphNodeStyle {
  /** SVG fill for the node circle (hex/rgb — consumer-supplied palette). */
  color: string
}

export interface ForceGraphEdgeStyle {
  dashed?: boolean
  /** 0–1 stroke opacity when not dimmed (default 0.6). */
  opacity?: number
}

export interface ForceGraphLabels {
  minEdges: string          // "Min edges"
  edgeLength: string        // "Edge length"
  zoom: string              // "Zoom"
  reset: string             // "Reset"
  expandSelected: string    // "Expand node"
  maximize: string          // "Expand graph"
  minimize: string          // "Collapse graph"
}

export interface ForceGraphProps {
  nodes: ForceGraphNode[]
  edges: ForceGraphEdge[]
  nodeStyles: Record<string, ForceGraphNodeStyle>
  edgeStyles?: Record<string, ForceGraphEdgeStyle>
  selectedId?: string | null
  onSelectNode?: (id: string) => void
  /** When set, selection shows an Expand button and double-click expands. */
  onExpandNode?: (id: string) => void
  /** Node id currently being expanded (renders its Expand button disabled). */
  expandingId?: string | null
  /** Status line above the canvas; consumer formats counts + hints. */
  statusText?: string
  /** Legend entries; omit to hide the legend. */
  legend?: Array<{ kind: string; label: string }>
  /** Control captions — consumer passes translated strings; en defaults built in. */
  labels?: Partial<ForceGraphLabels>
  /** Canvas height class when not maximized (default 'h-[60vh]'). */
  heightClassName?: string
  className?: string
}

export function ForceGraph(props: ForceGraphProps): JSX.Element
```

**Implementation guide.** Start from a copy of docint's `EntityGraph.tsx` and apply these transformations (each shown concretely). Everything not mentioned ports unchanged: the constants (`WIDTH`/`HEIGHT`/zoom bounds/`DRAG_THRESHOLD`/spread bounds), `View`, the `runLoop` rAF pattern, the non-passive wheel binding via `setSvgRef`, background pan handlers, node drag handlers, drag-vs-click threshold logic, min-degree filter, spread slider effect, maximize overlay effects (Escape, body-scroll lock, auto-collapse on empty), and the controls row markup.

- [ ] **Step 1: Copy the reference and strip docint-specifics**

```bash
cp /Users/himarc/dev/nos-tromo/infra/docint/frontend/src/components/analysis/EntityGraph.tsx src/graph/ForceGraph.tsx
```

Then:
1. Replace the imports block with:

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '../cn'
import {
  createForceSimulation,
  type ForceLink,
  type ForceNode
} from './forceGraph'
import { seedPositions } from './mergePositions'
```

2. Delete the `PALETTE` array, `colorForType`, `radiusForMentions`, `NodeMeta`, the docint `Props` interface, and the `GraphTopKControl` import + its render block (the node-count control is a docint-app concern; consumers can render their own controls next to the component).
3. Rename the exported function to `ForceGraph` and use the `ForceGraphProps` interface from the Interfaces block above (paste it plus `ForceGraphNode`/`ForceGraphEdge`/`ForceGraphNodeStyle`/`ForceGraphEdgeStyle`/`ForceGraphLabels` verbatim at the top of the file, exported).
4. Add the label defaults right below the interfaces:

```tsx
const DEFAULT_LABELS: ForceGraphLabels = {
  minEdges: 'Min edges',
  edgeLength: 'Edge length',
  zoom: 'Zoom',
  reset: 'Reset',
  expandSelected: 'Expand node',
  maximize: 'Expand graph',
  minimize: 'Collapse graph'
}
```

and inside the component `const L = { ...DEFAULT_LABELS, ...labels }`; replace every hardcoded control caption/aria-label with the matching `L.*`.

5. Radius: replace `radiusForMentions(n.mentions)` with

```tsx
function radiusForSize(size: number | undefined): number {
  return Math.min(34, 7 + Math.sqrt(Math.max(1, size ?? 1)) * 2.4)
}
```

- [ ] **Step 2: Sim construction with merged positions**

Replace docint's `useMemo` sim builder (its `phyllotaxisSeed` + meta maps) with a version that (a) persists positions across rebuilds via a ref, (b) seeds through `seedPositions`:

```tsx
const positionsRef = useRef<Map<string, { x: number; y: number }>>(new Map())

const sim = useMemo(() => {
  const seeds = seedPositions(visibleNodes, visibleEdges, positionsRef.current, CENTER_X, CENTER_Y)
  const simNodes: ForceNode[] = visibleNodes.map((n) => {
    const p = seeds.get(n.id)!
    return { id: n.id, x: p.x, y: p.y, vx: 0, vy: 0, r: radiusForSize(n.size) }
  })
  const simLinks: ForceLink[] = visibleEdges.map((e) => ({
    source: e.source,
    target: e.target,
    weight: e.weight ?? 1
  }))
  return createForceSimulation(simNodes, simLinks, { centerX: CENTER_X, centerY: CENTER_Y })
}, [visibleNodes, visibleEdges])
```

Inside `runLoop`'s `step`, after the tick loop, snapshot positions so the next rebuild can reuse them:

```tsx
for (const n of sim.nodes) positionsRef.current.set(n.id, { x: n.x, y: n.y })
```

Also delete docint's viewport-reset effect (`useEffect(() => setView(...), [sim])`) — with position merging, resetting the viewport on every data growth would yank the camera; keep the viewport as-is and let Reset restore it.

Note: node/edge props referenced by the sim `useMemo` must be referentially stable per data change (consumers pass mapper output memoized on the API payload) — document this in the component doc comment.

- [ ] **Step 3: Rendering — themed edges, arrowheads, style maps**

1. Add one `<defs>` block as the first child of the `<g transform={transform}>` group:

```tsx
<defs>
  <marker
    id="fg-arrow"
    viewBox="0 0 10 10"
    refX="9"
    refY="5"
    markerWidth="7"
    markerHeight="7"
    orient="auto-start-reverse"
    className="fill-muted-foreground"
  >
    <path d="M 0 0 L 10 5 L 0 10 z" />
  </marker>
</defs>
```

2. Replace the edge `<line>` render with (kind-styled, arrowheaded, endpoint shortened so arrows don't bury under the node circle):

```tsx
{visibleEdges.map((e, i) => {
  const a = sim.nodeById(e.source)
  const b = sim.nodeById(e.target)
  if (!a || !b) return null
  const style = edgeStyles?.[e.kind]
  const incident = selectedId != null && (e.source === selectedId || e.target === selectedId)
  const dimmed = selectedId != null && !incident
  const dx = b.x - a.x
  const dy = b.y - a.y
  const dist = Math.hypot(dx, dy) || 1
  // Pull the tip back to the target's rim so the arrowhead stays visible.
  const tx = e.directed ? b.x - (dx / dist) * (b.r + 2) : b.x
  const ty = e.directed ? b.y - (dy / dist) * (b.r + 2) : b.y
  return (
    <line
      key={`${e.source}->${e.target}:${e.kind}:${i}`}
      x1={a.x}
      y1={a.y}
      x2={tx}
      y2={ty}
      className="stroke-muted-foreground"
      strokeOpacity={dimmed ? 0.15 : (style?.opacity ?? 0.6)}
      strokeWidth={Math.min(4, 0.6 + Math.log2((e.weight ?? 1) + 1)) / view.k}
      strokeDasharray={style?.dashed ? `${4 / view.k} ${3 / view.k}` : undefined}
      markerEnd={e.directed ? 'url(#fg-arrow)' : undefined}
    />
  )
})}
```

3. Node render: fill from `nodeStyles[n.kind]?.color ?? 'currentColor'`; the circle stroke and the label text switch to token classes:

```tsx
<circle
  r={r}
  fill={nodeStyles[n.kind]?.color ?? 'currentColor'}
  fillOpacity={isSelected ? 1 : 0.85}
  className={isSelected ? 'stroke-foreground' : 'stroke-border'}
  strokeWidth={(isSelected ? 3 : 1.5) / view.k}
/>
<text
  y={r + 11 / view.k}
  textAnchor="middle"
  fontSize={11 / view.k}
  className="pointer-events-none fill-muted-foreground"
>
  {n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}
</text>
```

`aria-label` becomes `` `${n.label} (${n.kind})` ``; `handleSelect`/keyboard handlers call `onSelectNode?.(id)` directly (no `selectableKeyById` indirection — delete it, and derive `selectedId` straight from the prop).

4. Container/background surfaces: replace every `bg-zinc-950` / `bg-zinc-900/90` with `bg-surface` (token) so the frame follows the theme. Legend renders from the `legend` prop (kind → `nodeStyles[kind].color` swatch + label) instead of computing types.

- [ ] **Step 4: Expand affordance**

1. Double-click: add to the node `<g>`:

```tsx
onDoubleClick={() => {
  if (onExpandNode) onExpandNode(n.id)
}}
```

2. Selection expand button — render after the maximize button, only when a node is selected and `onExpandNode` is set:

```tsx
{selectedId && onExpandNode && (
  <button
    type="button"
    disabled={expandingId === selectedId}
    onClick={() => onExpandNode(selectedId)}
    className="absolute bottom-2 left-2 z-10 rounded-md border border-border bg-surface/90 px-2 py-1 text-xs text-foreground disabled:opacity-40"
  >
    {L.expandSelected}
  </button>
)}
```

- [ ] **Step 5: Write the component tests**

Create `src/graph/ForceGraph.test.tsx` (happy-dom; rAF exists there, and the sim renders the seed layout synchronously on first paint):

```tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ForceGraph } from './ForceGraph'

const NODES = [
  { id: 'a', label: 'Alpha', kind: 'author' },
  { id: 'b', label: 'Beta', kind: 'topic', size: 4 }
]
const EDGES = [{ source: 'a', target: 'b', kind: 'mentions', directed: true, weight: 2 }]
const STYLES = { author: { color: '#7c3aed' }, topic: { color: '#4ade80' } }

describe('ForceGraph', () => {
  it('renders one circle per node and one line per edge', () => {
    const { container } = render(<ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} />)
    expect(container.querySelectorAll('g[role="button"]')).toHaveLength(2)
    expect(container.querySelectorAll('line')).toHaveLength(1)
  })

  it('draws an arrowhead marker on directed edges only', () => {
    const { container } = render(<ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} />)
    expect(container.querySelector('line')?.getAttribute('marker-end')).toBe('url(#fg-arrow)')
    const { container: c2 } = render(
      <ForceGraph
        nodes={NODES}
        edges={[{ source: 'a', target: 'b', kind: 'friends' }]}
        nodeStyles={STYLES}
      />
    )
    expect(c2.querySelector('line')?.getAttribute('marker-end')).toBeNull()
  })

  it('click selects a node', () => {
    const onSelect = vi.fn()
    render(<ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} onSelectNode={onSelect} />)
    fireEvent.click(screen.getByRole('button', { name: /Alpha/ }))
    expect(onSelect).toHaveBeenCalledWith('a')
  })

  it('shows the Expand button for the selected node and fires onExpandNode', () => {
    const onExpand = vi.fn()
    render(
      <ForceGraph
        nodes={NODES}
        edges={EDGES}
        nodeStyles={STYLES}
        selectedId="a"
        onExpandNode={onExpand}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Expand node' }))
    expect(onExpand).toHaveBeenCalledWith('a')
  })

  it('double-click expands', () => {
    const onExpand = vi.fn()
    render(<ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} onExpandNode={onExpand} />)
    fireEvent.doubleClick(screen.getByRole('button', { name: /Beta/ }))
    expect(onExpand).toHaveBeenCalledWith('b')
  })

  it('hides the Expand button when onExpandNode is not provided', () => {
    render(<ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} selectedId="a" />)
    expect(screen.queryByRole('button', { name: 'Expand node' })).toBeNull()
  })

  it('renders the legend from the prop', () => {
    render(
      <ForceGraph
        nodes={NODES}
        edges={EDGES}
        nodeStyles={STYLES}
        legend={[{ kind: 'author', label: 'Authors' }]}
      />
    )
    expect(screen.getByText('Authors')).toBeInTheDocument()
  })

  it('grows without unmounting existing nodes (merge path)', () => {
    const { container, rerender } = render(
      <ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} />
    )
    rerender(
      <ForceGraph
        nodes={[...NODES, { id: 'c', label: 'Gamma', kind: 'author' }]}
        edges={[...EDGES, { source: 'a', target: 'c', kind: 'mentions' }]}
        nodeStyles={STYLES}
      />
    )
    expect(container.querySelectorAll('g[role="button"]')).toHaveLength(3)
  })

  it('uses translated labels when provided', () => {
    render(
      <ForceGraph nodes={NODES} edges={EDGES} nodeStyles={STYLES} labels={{ reset: 'Zurücksetzen' }} />
    )
    expect(screen.getByRole('button', { name: 'Zurücksetzen' })).toBeInTheDocument()
  })
})
```

- [ ] **Step 6: Run tests, iterate until green**

Run: `pnpm test -- src/graph`
Expected: PASS (forceGraph 8, mergePositions 5, ForceGraph 9). Fix compile/behavior fallout from the transformation until green.

- [ ] **Step 7: Lint + typecheck**

Run: `pnpm lint && pnpm typecheck`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/graph/ForceGraph.tsx src/graph/ForceGraph.test.tsx
git commit -m "feat: ForceGraph primitive — themed SVG force graph with select/expand and incremental merge"
```

---

### Task 4: Export, build, docs, version bump

**Files:**
- Modify: `src/index.ts` (append exports)
- Modify: `package.json` (version `0.1.0` → `0.3.0`; check the actual current version first and bump minor from it)
- Modify: `README.md` (add a ForceGraph section)
- Modify: `dist/` (regenerated, committed)

**Interfaces:**
- Produces: `import { ForceGraph, type ForceGraphNode, type ForceGraphEdge, type ForceGraphProps, type ForceGraphNodeStyle, type ForceGraphEdgeStyle, type ForceGraphLabels } from '@infra/ui'` — the exact import surface the chorus frontend plan consumes.

- [ ] **Step 1: Export from the package index**

Append to `src/index.ts`:

```ts
export {
  ForceGraph,
  type ForceGraphNode,
  type ForceGraphEdge,
  type ForceGraphProps,
  type ForceGraphNodeStyle,
  type ForceGraphEdgeStyle,
  type ForceGraphLabels,
} from './graph/ForceGraph'
```

- [ ] **Step 2: Verify the full gate**

Run: `pnpm lint && pnpm typecheck && pnpm test && pnpm build`
Expected: all clean; `dist/` regenerated (confirm `git status` shows `dist/` changes).

- [ ] **Step 3: README + version bump**

In `package.json`, bump `"version"` to `0.3.0`. In `README.md`, add a short `### ForceGraph` subsection to the primitives list: one paragraph (interactive SVG force graph — zoom/pan/drag/select/expand, incremental merge, token-themed interior) and a minimal usage snippet:

```tsx
<ForceGraph
  nodes={[{ id: 'a', label: 'Alpha', kind: 'author' }]}
  edges={[]}
  nodeStyles={{ author: { color: '#7c3aed' } }}
  onSelectNode={setSelected}
/>
```

Note in the section that new nodes merged into `nodes` keep the existing layout (expansion UX) and that `labels` accepts translated control captions.

- [ ] **Step 4: Commit (source + dist together, per the committed-dist rule)**

```bash
git add src/index.ts package.json README.md dist/
git commit -m "feat: export ForceGraph; bump to 0.3.0"
```

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feature/forcegraph
gh pr create --title "feat: ForceGraph primitive (force-sim SVG graph)" --body "Adds the shared ForceGraph primitive (ported from docint's entity-graph engine, generalized + themed + incremental merge). Consumed by chorus's reactive graph refactor. docint migration deferred. Release: v0.3.0 minted on merge by the release-tag workflow."
```

On merge, the shared release-tag workflow mints `v0.3.0` from the declared version — verify the tag exists (`git fetch --tags && git tag -l v0.3.0`) before starting the chorus frontend plan.
