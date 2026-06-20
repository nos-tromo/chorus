# Landing Dashboard — Graph Diagnostics — Design Spec

**Date:** 2026-06-20
**Status:** Approved (design)
**Branch:** `feat/react-spa-frontend` (follow-on to the React SPA migration / ADR 0015)

## Goal

Turn the sparse Landing screen into a graph-overview **dashboard**: a static
(load-once) snapshot of the Neo4j graph — counts, named highlights, health
metrics, and a small platform chart — using the full page width via a card grid.
Backed by one new endpoint. Decided over an agent-screen right rail
(`AskUserQuestion`, 2026-06-20).

## Decisions (locked)

- **Placement:** the Landing page (`routes/Landing.tsx`), not a right rail. The
  agent screen's empty third is unchanged (deferred stretch can address it
  later).
- **Content:** counts + **named highlights** + a mini chart. Named highlights
  (top entities, top authors) are shown deliberately — the source data is public,
  visibility is required.
- **`GET /stats` is authenticated and §76-audited** (a lightweight audit entry:
  principal + "diagnostics viewed"). Consistent with the rest of the data
  surface; the public-source nature is the rationale for showing names, not for
  skipping the audit.
- **Static:** fetched once via React Query (`staleTime ≈ 60s`), not live.
- **Airgap:** `recharts` is bundled (no runtime fetch), matching the
  docint/Nextext version.

## Backend — `GET /stats`

New `chorus/api/routers/stats.py` + `chorus/queries/stats.cypher`, registered in
`api/main.py`; module added to `tests/conftest.py` `_CHORUS_ENV_MODULES`.

- **Auth:** `Depends(resolve_principal)` (like the tools). Lightweight §76 audit
  log entry on each call (principal + a `diagnostics` action; the audit logger in
  `chorus/audit/logger.py` is the seam — reuse its existing API, do not invent a
  parallel log).
- **Query:** one round-trip using `CALL {}` subqueries returning a single row:
  - **node counts**: `posts` (`:Post`), `authors` (`:Author`), `entities`
    (`:Entity`), `hashtags` (`:Hashtag`), `groups` (`:Group`), `platforms`
    (`:Platform`), `aliases` (`:Alias`)
  - **edge counts**: `mentions` (`:MENTIONS`), `authored` (`:AUTHORED`),
    `follows` (`:FOLLOWS`), `friends` (`:FRIENDS_WITH`), `resolved` (`:RESOLVED_TO`)
  - **top_entities** (≤5): `(:Post)-[:MENTIONS]->(:Alias)`, `OPTIONAL MATCH
    (:Alias)-[:RESOLVED_TO]->(:Entity)`, group by `coalesce(entity.canonical_name,
    alias.surface_form)`, count posts, order desc — `{name, count}`
  - **top_authors** (≤5): `(:Author)-[:AUTHORED]->(:Post)`, count, order desc —
    `{author_id, label (display_name||handle||id), count}`
  - **posts_by_platform**: `(:Post)-[:ON_PLATFORM]->(:Platform)` count per
    platform — `[{platform, count}]`
  - **latest_ingested_at**: `max(p.ingested_at)` over `:Post` (nullable)
  - **resolution_coverage**: `{resolved_aliases, total_aliases}` (aliases with a
    `RESOLVED_TO` edge / all aliases) → the UI computes the %.
- **Response model** `StatsOut` (Pydantic): `counts: {…}`, `edges: {…}`,
  `top_entities: [{name, count}]`, `top_authors: [{author_id, label, count}]`,
  `posts_by_platform: [{platform, count}]`, `latest_ingested_at: str | None`,
  `resolution: {resolved_aliases, total_aliases}`.
- **Empty graph:** all counts `0`, lists `[]`, `latest_ingested_at: null`,
  `resolution {0,0}` — never an error.

## Frontend — Landing becomes a dashboard

- **Dep:** add `recharts` (match docint/Nextext: `^3.8.1`); `pnpm install`.
- `src/api/stats.ts` → `fetchStats = () => apiGet<GraphStats>('/stats')`;
  `src/hooks/useStats.ts` → `useQuery(['stats'], fetchStats, { staleTime: 60_000 })`.
- `src/api/types.ts` → `GraphStats` (mirror `StatsOut` field names exactly).
- Enrich `routes/Landing.tsx` (keep the existing health / tools / ingestion-status
  sections) with a responsive **card grid** (`@infra/ui` `Card`, design tokens,
  full width):
  - **KPI count cards** — nodes + edges by type.
  - **Top entities** and **top authors** — small lists (name + count); reuse
    `DataTable` or a light list.
  - **Health KPIs** — latest ingestion time (formatted) + resolution coverage %
    (computed from `resolution`).
  - **Posts-per-platform** — a small `recharts` bar chart, accent-colored.
  - Loading → `Spinner`; error → `Banner variant="danger"`; empty graph → a
    "no data yet" state (zeros render fine; show a hint).
- All labels via `useT()`; add `dashboard.*` i18n keys to **both** `en.ts` and
  `de.ts` (keep the parity test green).

## Testing

- **Backend:** integration test (`neo4j` testcontainer, like the tool tests) —
  seed a tiny graph (a couple posts/authors/aliases/entities/platforms + a
  resolve edge), call `/stats`, assert the counts, top lists, platform breakdown,
  and resolution numbers; plus an empty-graph case (all zeros, no error). Confirm
  the audit entry is written.
- **Frontend:** Landing test extended — mock `useStats` to return a populated
  `GraphStats`; assert KPI counts, top-entity/author rows, the chart container,
  and the resolution % render; plus loading, error, and empty (zeros) states.
- `pnpm typecheck`/`lint`/`test`/`build` + `uv run pytest`/`ruff`/`mypy` green.

## Out of scope

Live/streaming stats; per-screen right rails; widening the agent/tool screens
(separate deferred item); historical/time-series metrics; clickable drill-down
from dashboard tiles into the tools (nice future follow-up).
