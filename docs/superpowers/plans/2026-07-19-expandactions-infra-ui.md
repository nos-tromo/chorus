# ExpandActions API (infra-ui v0.5.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an additive multi-action expand API to `ForceGraph` so a consumer can offer several expansion choices per selected node, released as `v0.5.0`.

**Architecture:** New optional `expandActions`/`onExpandAction` props supersede the single `onExpandNode` affordance when present; rendering reuses the existing selection chip row and double-click path. Purely additive — `onExpandNode` consumers (docint) see zero behavior change.

**Tech Stack:** React 19, TypeScript, vitest + happy-dom, tsup. No new dependencies.

**Repo:** ALL work in `/Users/himarc/dev/nos-tromo/infra/infra-ui`. Setup: `git fetch origin main -q && git checkout -b feat/forcegraph-expand-actions origin/main`.

## Global Constraints

- Strictly additive API: no existing prop removed or narrowed; a consumer passing only `onExpandNode` behaves byte-for-byte as v0.4.0.
- No new runtime dependencies; no Math.random()/Date.now().
- `dist/` regenerated and committed with the final src commit; `pnpm test && pnpm lint && pnpm typecheck && pnpm build && pnpm demo:build` all green before the release commit.
- Version bumps to `0.5.0`; tags are hand-cut post-merge by the controller (do NOT tag).
- Do NOT push or open a PR (controller handles both).

---

### Task 1: expandActions rendering + dispatch

**Files:**
- Modify: `src/graph/ForceGraph.tsx`
- Test: `src/graph/ForceGraph.test.tsx`

**Interfaces:**
- Produces (exact — the chorus plan consumes these names):

```ts
export interface ForceGraphExpandAction {
  id: string
  label: string
}
// New optional ForceGraphProps members:
expandActions?: ForceGraphExpandAction[]
onExpandAction?: (actionId: string, nodeId: string) => void
```

Behavior contract:
1. When `expandActions` is a non-empty array AND `onExpandAction` is set AND exactly one node is selected: render one chip button per action (same styling/position as the current Expand button — extend the bottom-left chip row), text/aria = `action.label`, each `disabled={expandingId === selectedId}`; clicking fires `onExpandAction(action.id, selectedId)`. The single `onExpandNode` Expand button is NOT rendered in this mode even if `onExpandNode` is also supplied (`expandActions` wins — document in the prop doc comment).
2. Double-click on a node: if `expandActions` non-empty + `onExpandAction` present → `onExpandAction(firstAction.id, nodeId)`; else existing `onExpandNode` path unchanged.
3. `expandActions` absent/empty → exactly v0.4.0 behavior (single Expand button from `onExpandNode`).
4. Zero or multiple nodes selected → no expand chips (matches current gating).

- [ ] **Step 1: Write the failing tests** — add a `describe('expandActions', ...)` block to `ForceGraph.test.tsx` (reuse the file's NODES/EDGES/STYLES fixtures):

```tsx
const ACTIONS = [
  { id: 'topics', label: 'Expand topics' },
  { id: 'ties', label: 'Expand ties' }
]

it('renders one chip per action when exactly one node is selected', ...)
  // selectedIds={['a']}, expandActions=ACTIONS, onExpandAction=spy
  // → buttons 'Expand topics' and 'Expand ties' both present; no 'Expand node' button

it('fires onExpandAction with action id and node id', ...)
  // click 'Expand ties' → spy called with ('ties', 'a')

it('hides action chips at zero and at two selected', ...)
  // selectedIds={[]} → no chips; selectedIds={['a','b']} → no chips

it('disables chips while the selected node is expanding', ...)
  // expandingId='a', selectedIds=['a'] → both buttons disabled

it('double-click fires the FIRST action', ...)
  // dblclick node b → spy called with ('topics', 'b')

it('expandActions wins over onExpandNode when both supplied', ...)
  // both props set → action chips render, 'Expand node' absent; dblclick fires onExpandAction not onExpandNode

it('absent expandActions preserves v0.4.0 single-expand behavior', ...)
  // only onExpandNode → 'Expand node' button renders and dblclick fires it (regression)
```

- [ ] **Step 2: Run to verify failure** (`pnpm test -- src/graph/ForceGraph.test.tsx`) — the 7 new tests fail (unknown props / missing buttons).

- [ ] **Step 3: Implement** in `ForceGraph.tsx`:
  1. Export `ForceGraphExpandAction`; add the two props to `ForceGraphProps` with doc comments (note the supersedes-onExpandNode rule and the double-click-fires-first rule).
  2. Derive `const actionsActive = !!(expandActions && expandActions.length > 0 && onExpandAction)`.
  3. Chip row: where the single Expand button renders (`selectedIds.length === 1`), branch: `actionsActive` → map `expandActions` to buttons (key `action.id`, same className as the Expand chip, `disabled={expandingId === selectedId}`, onClick `onExpandAction(action.id, selectedId)`); else existing `onExpandNode` button unchanged.
  4. Node `onDoubleClick`: `actionsActive ? onExpandAction(expandActions[0].id, n.id) : onExpandNode?.(n.id)`.

- [ ] **Step 4: Run to green** — new 7 pass, all existing pass (`pnpm test`).
- [ ] **Step 5: Lint + typecheck**; **Step 6: Commit** `feat: expandActions — multiple expand choices per selected node`

---

### Task 2: Export, release chores

**Files:**
- Modify: `src/index.ts` (add `type ForceGraphExpandAction` to the ForceGraph export block)
- Modify: `package.json` (version → `0.5.0`), `README.md` (one line in the ForceGraph section: multiple expand actions per node kind via `expandActions`)
- Modify: `dist/` (regenerated)

- [ ] **Step 1: Export the type**; **Step 2: Full gate** `pnpm test && pnpm lint && pnpm typecheck && pnpm build && pnpm demo:build` (demo keeps using `onExpandNode` — that's the point: it must still work); **Step 3: bump version + README line**; **Step 4: Commit** `feat: export ForceGraphExpandAction; bump to 0.5.0` (dist/ folded in). Do not push.
