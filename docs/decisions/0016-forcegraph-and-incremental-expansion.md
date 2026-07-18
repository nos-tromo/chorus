# 0016. ForceGraph rendering and incremental expand-on-click exploration

## Context

ADR 0015 shipped the React SPA's two `*_around` tools (`network_around`,
`social_network_around`) as one-shot Cytoscape.js renders: a fixed-depth,
fixed-limit query returns `{nodes, edges}`, Cytoscape lays it out once, and
the user's only recourse for "what's beyond this boundary" is re-running the
tool with a larger `depth`/`limit` and losing their current view. There is no
notion of clicking a node to pull in its neighborhood incrementally, and the
agent's inline answers (`POST /agent/query`) carried no graph payload at all
— graph-shaped answers were text-only even when the underlying tool returned
`{nodes, edges}`.

Full design context, including the exploration model this ADR implements
and the alternatives evaluated up front, is recorded in
`docs/superpowers/specs/2026-07-18-reactive-graph-exploration-design.md`
(spec branch: `docs/reactive-graph-exploration-design` — unmerged at the time
this ADR lands; the path is stable across the eventual merge).

## Decision

**Renderer: shared `@infra/ui` `<ForceGraph>` (v0.3.0), a port of docint's
dependency-free SVG force-simulation engine, generalized for chorus.**
Cytoscape is removed entirely — no `cytoscape` dependency, no
`<GraphCanvas>`, no Cytoscape stylesheets. `<ForceGraph>` takes per-kind style
maps (chorus adds `topic`/`author` for the entity family and `author` rings
for the social family, alongside docint's own kinds), draws directed edges
with SVG-marker arrowheads (used for `FOLLOWS`; `FRIENDS_WITH` renders
undirected per the existing data-model convention), and themes its interior
off token classes so it is dark-mode capable without bespoke chorus CSS. The
same component instance merges new nodes/edges into a running layout
position-preserving — expansion adds to the graph in place rather than
re-running the force sim from scratch, which is what makes click-to-expand
feel incremental instead of a fresh render.

**Expansion: two new `@audited` registry tools**, `expand_network_node` and
`expand_social_node` (`chorus/tools/expand_network_node.py`,
`chorus/tools/expand_social_node.py`), each a normal tool-registry citizen —
Pydantic in/out models, a versioned `.cypher` template, `@register_tool`,
audit logging. Because they are ordinary tools, they are agent-callable like
any other, and each invocation produces its own §76 BDSG audit row (one row
per expansion click, same as any other tool call) — no special-cased,
audit-exempt "just a UI fetch" path exists anywhere in this design.

**Agent wiring — trace payloads (a), not frontend replay (b).** The agent's
tool-calling loop already returns a `TraceStep` per tool invocation
(ADR 0009). Graph-shaped tool results (`network_around`,
`social_network_around`, and now the two expand tools) attach their
`{nodes, edges}` payload directly onto the corresponding `TraceStep.result`,
capped at the existing ≤500-node ceiling. The frontend's new
`AgentGraphCard` renders straight from that payload — no separate fetch, no
re-derivation of what the agent already computed and returned. The rejected
alternative (b) — the frontend re-issuing/replaying the agent's tool calls
itself to reconstruct a graph — was rejected because it duplicates the
audited call the agent already made under a different (unaudited-adjacent)
code path and can drift from what the agent actually saw.

## Alternatives considered

- **Keep Cytoscape, add layout/expansion via Cytoscape plugins
  (`cytoscape-expand-collapse`, incremental `cose-bilkent` reruns).** Rejected:
  reintroduces the exact extension-package airgap surface ADR 0015 deliberately
  avoided ("no Cytoscape extension packages are loaded"), and still leaves
  chorus maintaining its own rendering engine independent of the rest of the
  `@infra/ui`-consuming family.
- **Per-app engine copies** (chorus forks/reimplements docint's force-sim
  engine locally instead of promoting it into `@infra/ui`). Rejected: the
  engine is generic (SVG force simulation over `{nodes, edges}`, no
  chorus-specific or docint-specific assumptions beyond style maps); copying
  it means every future engine fix (e.g. an arrowhead-rendering or
  merge-stability bug) has to be ported by hand across repos instead of
  landing once in the shared package and being picked up by a version bump.
- **Frontend replay wiring (b)** for the agent inline graphs — see above;
  rejected in favor of (a).

## Consequences

- `cytoscape`, `@types/cytoscape`, `<GraphCanvas>`, and the Cytoscape
  stylesheets are gone from the frontend dependency tree and codebase.
- The existing depth-2 traversal caps on `network_around` /
  `social_network_around` are **not** raised — expansion supersedes the need
  to widen the initial fetch; a user who wants more graph clicks for it
  incrementally (and each click is its own audited, capped call) rather than
  the tool returning an unbounded neighborhood up front.
- Every expansion click is a real tool call end-to-end (registry → Cypher →
  audit log), so §76 audit coverage for graph exploration is exactly as
  complete as for the original `*_around` tools — there is no unaudited
  "just render more" path.
- docint still renders its own graphs with its pre-existing engine
  integration; migrating docint onto the newly-shared `@infra/ui`
  `<ForceGraph>` (as opposed to chorus adopting the port of docint's engine)
  is explicitly deferred — no docint-side change lands as part of this work.

## Future: unified explorer

Merging the two graph screens (`ToolNetwork`, `ToolSocial`) into one combined
explorer was considered and deliberately deferred. It stays additive rather
than becoming a rewrite precondition because the pieces already line up: the
renderer (`ForceGraph`) is family-agnostic, so it doesn't need to change to
serve a merged canvas; both families share the namespaced `author:<id>` node
id space, so a merged canvas dedupes authors for free via the existing
`mergeGraph` logic with no new identity-reconciliation code; the expand tools
are node-scoped, so an author node can offer both a "topics" (`expand_network_node`)
and a "ties" (`expand_social_node`) expansion with zero backend change; and
the edge kinds don't collide (`mentions` vs. `follows`/`friends`), so the two
families' style maps simply concatenate rather than needing a merge policy
today. What a future merge would add fresh, with nothing here needing to be
undone first: a union node view-model, a kind/color precedence policy for
nodes that are simultaneously social neighbors and mentioning authors, one
combined screen/hook, and a dual-expand affordance on shared nodes. That
future work supersedes the per-family hooks (`useGraphExplorer` as
instantiated per screen today); the pure, reusable pieces underneath —
`ForceGraph`, the merge logic, the style maps, the expand tools — carry over
unchanged.
