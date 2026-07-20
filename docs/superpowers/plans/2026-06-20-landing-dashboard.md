# Landing Dashboard (Graph Diagnostics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Add a `GET /stats` graph-diagnostics endpoint and turn the Landing screen into a dashboard (counts, named highlights, resolution %, posts-per-platform chart).

**Architecture:** One backend endpoint (`routers/stats.py` + `queries/stats.cypher`, authed + §76-audited) returns a one-row aggregate snapshot; the React Landing screen fetches it via React Query and renders a card grid + a `recharts` bar chart.

**Tech Stack:** FastAPI + Neo4j (Python 3.12, `uv`); React 19 + Vite + Tailwind v4 + `@infra/ui` + `@tanstack/react-query` + `recharts`.

## Global Constraints

- Repo root `../chorus`; importable package is the nested `chorus/`; SPA in `frontend/`.
- Branch: `feat/react-spa-frontend` (continue on it).
- `/stats` is **authed** (`Depends(resolve_principal)`) + writes a **lightweight §76 audit entry** via the existing `chorus/audit/logger.py` API (do not invent a parallel logger). API stays at root path (no `/api` prefix); add `/stats` to the Vite + nginx proxy prefix lists.
- Empty graph → all zeros / empty lists / null — never an error.
- SPA sends no identity header (dev uses `CHORUS_DEFAULT_IDENTITY`); same-origin, no CORS.
- Airgap: `recharts` bundled (no runtime fetch); `pnpm install --frozen-lockfile` must work (commit the updated lockfile).
- i18n: every new UI string via `useT()`; add keys to BOTH `en.ts` and `de.ts` (parity test must stay green).
- TDD; keep `pnpm typecheck`/`lint`/`test`/`build` and `uv run pytest`/`ruff`/`mypy` green; one commit per task.

---

### Task 1: Backend `GET /stats` (diagnostics aggregate)

**Files:**
- Create: `chorus/queries/stats.cypher`, `chorus/api/routers/stats.py`
- Modify: `chorus/api/main.py` (register router), `tests/conftest.py` (`_CHORUS_ENV_MODULES` += `"chorus.api.routers.stats"`)
- Test: `tests/integration/test_stats.py`

**Interfaces — Produces:** `GET /stats` → `StatsOut { counts: {posts,authors,entities,hashtags,groups,platforms,aliases}, edges: {mentions,authored,follows,friends,resolved}, top_entities: [{name, count}], top_authors: [{author_id, label, count}], posts_by_platform: [{platform, count}], latest_ingested_at: str|None, resolution: {resolved_aliases, total_aliases} }`. Authed (`resolve_principal`); writes a §76 audit entry.

- [ ] **Step 1 — failing integration test.** In `tests/integration/test_stats.py`, mirror the existing tool integration tests (reuse their neo4j testcontainer fixture + the migration apply + a seed helper). Seed a tiny graph: 2 authors, 3 posts on 2 platforms, 2 aliases (one `RESOLVED_TO` an entity), a couple `MENTIONS`. Call `GET /stats` (with the test's auth header / default identity). Assert: `counts.posts==3`, `counts.authors==2`, `counts.entities==1`, `edges.mentions>=2`, `top_authors[0].count` is the busiest author's post count, `posts_by_platform` sums to 3, `resolution.total_aliases==2` and `resolution.resolved_aliases==1`, `latest_ingested_at` is a non-empty ISO string. Add an **empty-graph** case (fresh DB) → all counts 0, lists `[]`, `latest_ingested_at is None`, `resolution=={0,0}`, status 200.
- [ ] **Step 2 — run, expect FAIL** (`uv run pytest tests/integration/test_stats.py -v`; 404 / import error).
- [ ] **Step 3 — write `queries/stats.cypher`**: a single statement using `CALL {}` subqueries (each returns one value/list), combined into one `RETURN` row with the fields above. Top entities: `MATCH (p:Post)-[:MENTIONS]->(al:Alias) OPTIONAL MATCH (al)-[:RESOLVED_TO]->(e:Entity) WITH coalesce(e.canonical_name, al.surface_form) AS name, count(p) AS c RETURN collect({name:name, count:c})[..5]` (order by c desc before collect). Top authors: `MATCH (a:Author)-[:AUTHORED]->(p:Post) WITH a, count(p) AS c ORDER BY c DESC LIMIT 5 RETURN collect({author_id:a.id, label:coalesce(a.display_name,a.handle,a.id), count:c})`. Resolution: `MATCH (al:Alias) RETURN count(al) AS total, count{ (al)-[:RESOLVED_TO]->() } ...` (use a form valid for the project's Neo4j 5.x). Keep each subquery independent so an empty graph yields 0/[].
- [ ] **Step 4 — write `routers/stats.py`**: `StatsOut` Pydantic model; `router = APIRouter(tags=["stats"])`; `@router.get("/stats", response_model=StatsOut)` with `principal: str = Depends(resolve_principal)`; load + run `queries/stats.cypher` via the shared driver/session (mirror how a tool/loads a template — see `chorus/tools/_template_loader.py` and an existing tool); map the single row to `StatsOut`. Write a lightweight audit entry using the existing audit logger API (inspect `chorus/audit/logger.py` + how tools call it via `chorus/tools/_audit.py`; reuse it — e.g. log principal + a `"diagnostics"`/`"stats"` action). If the audit API is awkward to call outside the tool decorator, log a minimal entry directly through the same logger instance on `app.state` — do NOT create a second audit store.
- [ ] **Step 5 — register** in `api/main.py` (`from chorus.api.routers import stats as _stats_router; app.include_router(_stats_router.router)`); add `"chorus.api.routers.stats"` to `tests/conftest.py` `_CHORUS_ENV_MODULES`.
- [ ] **Step 6 — run** `uv run pytest tests/integration/test_stats.py -v` → PASS; then full `uv run pytest -q`, `uv run ruff check .`, `uv run mypy .` → green.
- [ ] **Step 7 — commit** `feat(api): add authed, audited GET /stats graph-diagnostics endpoint`.

### Task 2: Frontend data layer + Landing dashboard

**Files:**
- Modify: `frontend/package.json` (+`recharts`), `frontend/src/api/types.ts` (+`GraphStats`), `frontend/src/i18n/en.ts` + `de.ts` (+`dashboard.*`), `frontend/src/routes/Landing.tsx`, `frontend/vite.config.ts` + `frontend/nginx/default.conf.template` (add `/stats` to the proxied prefixes)
- Create: `frontend/src/api/stats.ts`, `frontend/src/hooks/useStats.ts`, `frontend/src/components/StatChart.tsx` (recharts wrapper), `frontend/src/routes/Landing.test.tsx` additions
- Test: extend `frontend/src/routes/Landing.test.tsx`; add `frontend/src/hooks/stats.test.tsx` if useful

**Interfaces — Consumes:** `GET /stats` (Task 1). **Produces:** `fetchStats = () => apiGet<GraphStats>('/stats')`; `useStats()` → `useQuery({ queryKey: ['stats'], queryFn: fetchStats, staleTime: 60_000 })`; `GraphStats` type mirroring `StatsOut` exactly.

- [ ] **Step 1 — add `recharts`** (`^3.8.1`, match docint/Nextext) to package.json; `pnpm install` (updates lockfile). Add `/stats` to the Vite proxy prefix list and the nginx `default.conf.template` general-API proxy regex (so it proxies like the other API prefixes).
- [ ] **Step 2 — failing Landing test.** Extend `Landing.test.tsx`: mock `useStats` (or the network) to return a populated `GraphStats`; assert the KPI counts render (e.g. posts/authors/entities numbers), a top-entity name + a top-author label appear, the resolution-coverage % is shown, and the chart container renders (mock `recharts`'s `ResponsiveContainer` if happy-dom can't size it — assert the chart wrapper/test-id and that `posts_by_platform` data is passed). Add loading (Spinner), error (Banner), and empty (all-zero stats → "no data yet" hint) cases. Run → FAIL.
- [ ] **Step 3 — implement** `api/stats.ts`, `hooks/useStats.ts`, `api/types.ts` `GraphStats`. `StatChart.tsx`: a small `recharts` `BarChart` (posts-per-platform) in a `ResponsiveContainer`, accent-colored, with an empty-state guard. Enrich `Landing.tsx`: keep the existing health/tools/ingestion sections; add a responsive `@infra/ui` `Card` grid — KPI count cards (nodes + edges), top-entities + top-authors lists (name/label + count; a light list or `DataTable`), latest-ingestion + resolution-% KPIs, and `<StatChart data={stats.posts_by_platform} />`. `useStats` loading → `Spinner`; error → `Banner variant="danger"`; empty → a hint. All labels via `useT()`; add `dashboard.*` keys to en.ts + de.ts (real German; keep parity test green).
- [ ] **Step 4 — run** `pnpm typecheck` + `pnpm lint` + full `pnpm test` + `pnpm build` → green.
- [ ] **Step 5 — commit** `feat(frontend): landing dashboard — graph diagnostics + posts-per-platform chart`.

### Task 3: Docs

**Files:** Modify `chorus/CLAUDE.md` (Current state: mention the landing dashboard + `/stats`), `docs/decisions/0015-react-spa-frontend.md` (a short addendum note, or a one-line pointer) — keep it minimal and accurate.

- [ ] **Step 1** — add a concise mention of the landing dashboard + `GET /stats` to CLAUDE.md "Current state"; note `/stats` in the endpoint inventory if one exists. Keep ADR 0015 accurate (add a one-line note that the landing was later enriched into a diagnostics dashboard, or leave ADR as-is and rely on this spec). `uv run pre-commit run --all-files` → clean.
- [ ] **Step 2 — commit** `docs: note the landing diagnostics dashboard + /stats`.

## Final verification
- [ ] `cd frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build` — green.
- [ ] `uv run pytest && uv run ruff check . && uv run mypy .` — green (incl. the new integration test).
- [ ] Manual: `make up-dev` (or `pnpm dev`), load the landing page, confirm the dashboard renders against a seeded graph.
