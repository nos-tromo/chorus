# Chorus React SPA Frontend — Design Spec

**Date:** 2026-06-20
**Status:** Approved (design); implementation pending
**Supersedes:** the Streamlit UI under `chorus/chorus/ui/`

## 1. Goal & guardrails

Replace the Streamlit UI (`chorus/chorus/ui/`) with a React Single-Page
Application in `chorus/frontend/`, matching the established infra frontend
pattern (translator / docint / Nextext) and consuming the shared
`@infra/ui#v0.1.1` design system. **Full parity in one migration**: every
current screen is ported and Streamlit is removed entirely.

Hard guardrails:

- **Airgap-first.** The image builds on internet-connected CI
  (`pnpm install --frozen-lockfile`, `pnpm build`) and bakes the static bundle
  into an nginx image. The airgapped side loads the prebuilt image and fetches
  nothing at runtime. Cytoscape is pure JS; the Inter font ships via
  `@fontsource/inter` (no CDN). No runtime network calls.
- **Supply-chain / airgap security.** All base images in chorus's Dockerfiles
  are pinned by `@sha256:` digest (the backend already does this for the `uv`
  image; the new frontend Dockerfile does the same for node + nginx). This is
  a chorus-only change — translator's digests are handled separately by the
  maintainer.
- **No scope creep.** No new graph tools, no SSE (keep HTTP polling for jobs),
  no semantic search, no change to the trusted-header auth model, no runtime
  language toggle.

## 2. Stack & versions (pinned to the family)

| Concern | Choice |
|---|---|
| Runtime / package mgr | Node 20, pnpm 9.12.0 (`packageManager` field) |
| Framework | React 19.2 |
| Build | Vite 8, TypeScript 6 (strict) |
| Styling | Tailwind v4 (`@tailwindcss/postcss`), `@infra/ui#v0.1.1` |
| Accent | `:root { --app-accent: hsl(262 83% 58%) }` (violet) |
| Server state | `@tanstack/react-query` 5 |
| Routing | `react-router-dom` 7 |
| Graph viz | `cytoscape` core + a thin custom React wrapper (no `react-cytoscapejs` — avoids its stale React-19 peer-dep; built-in layouts only, no extensions) |
| Font | `@fontsource/inter` |
| Tests | Vitest 4 + happy-dom + `@testing-library/react` |

## 3. Repository changes

```
chorus/
  frontend/                         # NEW — the SPA
    index.html
    package.json + pnpm-lock.yaml
    vite.config.ts                  # dev proxy of API prefixes → :8000
    tsconfig.json (+ node)
    eslint.config.js
    postcss.config.js
    vitest.config.ts / test/setup.ts
    nginx/
      default.conf                  # (env-templated for upload limit)
      security-headers.conf         # hardened CSP (docint-derived)
    src/
      main.tsx, App.tsx, vite-env.d.ts
      styles/globals.css            # tailwind + @infra/ui/theme.css + @source + violet accent
      api/                          # client.ts, queryClient.ts, types.ts, per-domain modules
      config/                       # ConfigProvider (boots GET /config: language + ingestion flag)
      i18n/                         # typed catalog (en/de) + useT() hook
      layout/                       # Shell.tsx, Sidebar.tsx
      routes/                       # Router.tsx + one screen per page
      components/                   # ToolForm controls, DataTable, GraphCanvas, JobProgress, ToolTrace, ...
      hooks/                        # useHealth, useTools, useToolCall, useAgentQuery, useConfig, ingestion hooks
      lib/                          # cn, graph element mappers, formatting helpers
  chorus/ui/                        # DELETED (streamlit_app, client, pages/, network_dot, social_network_dot)
  chorus/api/routers/config.py      # NEW — GET /config
  chorus/api/main.py                # register config router
  docker/Dockerfile.frontend        # REWRITTEN — node build → nginx; node + nginx pinned by @sha256
  docker/compose.yaml               # frontend → nginx image, expose 80, retire CHORUS_UI_* env
  docker/compose.override.yaml      # publish ${CHORUS_FRONTEND_HOST_PORT:-8501}:80
  pyproject.toml                    # remove the streamlit `frontend` dependency group
  docs/decisions/0015-react-spa-frontend.md   # NEW ADR
```

## 4. App shell & routing

`App.tsx` → `QueryClientProvider` → `ConfigProvider` (fetches `/config` once at
boot, supplies language + `ingestion_enabled`) → `BrowserRouter` → `Shell`
(persistent `Sidebar` + scrollable `main`). Routes:

- `/` — Landing (backend health, registered tools list, ingestion status)
- `/agent` — NL agent chat
- `/ingestion` — ingestion UI (nav item shown only when `ingestion_enabled`)
- `/tools/posts-mentioning`
- `/tools/authors-mentioning`
- `/tools/author-activity`
- `/tools/authors-connected`
- `/tools/topic-cooccurrence`
- `/tools/network-around`
- `/tools/social-network-around`

Sidebar groups: **Agent** · **Entities** (posts / authors mentioning) ·
**Authors** (activity summary, connected-by-topic) · **Topics** (co-occurrence)
· **Networks** (the two graph tools) · **Ingestion** (conditional).

## 5. API client & data layer

Typed `fetch` wrapper: `apiGet` / `apiPost`, `ApiError {status, detail}`,
React-Query `queryClient` (staleTime 30s, no refetch-on-focus, no retry on 4xx).
Hooks per domain. Backend routes stay at root — **no `/api` prefix is added** —
and both Vite (dev) and nginx (prod) proxy the exact prefixes to the backend,
same-origin, so **no CORS** is needed.

Endpoint contract the client must cover (verified against the current backend):

| Hook | Method + path | Notes |
|---|---|---|
| `useHealth` | `GET /health` | unauth; `{status}` |
| `useConfig` | `GET /config` | **NEW**, unauth; `{language, ingestion_enabled, version?}` |
| `useTools` | `GET /tools` | unauth; `[{name, description, input_schema, output_schema}]` |
| `useToolCall` | `POST /tools/{name}` | auth; tool-specific in/out |
| `useAgentQuery` | `POST /agent/query` | auth; `{messages}` → `{answer, trace, truncated}` (non-streaming) |
| `useMigrations` | `GET /ingestion/migrations` | auth + gate; `{applied, pending}` |
| `migrate` | `POST /ingestion/migrate` | auth + gate; `{applied}` |
| `ingest` | `POST /ingestion/ingest` | auth + gate; multipart (`files`, `since?`, `then_resolve`) → `202 {job_id, status, kind}` |
| `resolve` | `POST /ingestion/resolve` | auth + gate; `202 {job_id, status, kind}` |
| `useJob` | `GET /ingestion/jobs/{id}` | auth + gate; poll until terminal |

**Auth — chorus follows docint's model, NOT Nextext's.** There are two identity
models in the family, and the distinction is load-bearing:

- **Trusted-header authenticated principal (chorus + docint).** `X-Auth-User`
  is a *genuinely authenticated* identity injected by the upstream Nginx/OIDC
  proxy. Both `chorus/api/auth/principal.py` and
  `docint/core/auth/principal.py` are identical seams (trusted header →
  `*_DEFAULT_IDENTITY` dev fallback → `401` fail-closed). docint's SPA sends
  **no** identity header and has **no** owner module — the proxy is the source
  of truth.
- **Client-minted anonymous owner (Nextext).** Nextext's SPA *mints* a random
  per-browser id (`identity/owner.ts` `resolveOwnerId()`) and sends it as
  `X-Auth-User` purely for lightweight per-browser job isolation on an
  unauthenticated deployment — there is no real authentication behind it.

The chorus SPA therefore **copies docint's headerless `api/client.ts`** and
sends **no** identity header. Copying Nextext's client-minted owner pattern
would be an **auth-bypass / audit-integrity break**: any browser could forge
the §76 BDSG-audited principal over Art. 9 data. The trust boundary is the
outer OIDC proxy, which **sets/overwrites** `X-Auth-User`; the SPA's nginx
forwards it unchanged; the browser never sets it. Development: the backend
resolves identity from `CHORUS_DEFAULT_IDENTITY=dev` (set in the dev compose
env). This retires the Streamlit-era `CHORUS_API_URL`, `CHORUS_UI_IDENTITY`,
and `CHORUS_UI_TIMEOUT_S`.

*Bonus: this is strictly more correct than today.* The Streamlit client makes
server-side httpx calls with a **fixed** `CHORUS_UI_IDENTITY`, so the real
per-request OIDC identity never reaches the backend audit log. With the SPA,
browser requests transit the OIDC proxy directly, so the **per-user**
principal reaches the backend — correct §76 audit attribution.

**Statelessness — no blocker.** The chorus backend is stateless except the
deliberately *ephemeral, single-worker, one-active-job* in-memory ingestion job
registry (`ingestion/jobs.py`; results are durable in Neo4j + the audit log,
the registry only carries poll progress). It runs as a **single** backend
container, so an nginx-fronted static SPA + that one backend is fully
compatible — no sticky sessions, the SPA polls `GET /ingestion/jobs/{id}` exactly
as Streamlit did. Streamlit's server-side `st.session_state` (chat turns, form
inputs) becomes **client-side React state**; chorus has no server-side UI
session manager to migrate (unlike docint's `session_manager.py`).

## 6. Screens & shared components

Reusable building blocks:

- **ToolForm controls** — `EntityInput`, `TimeRangeInputs` (from/to), `LimitField`,
  consistent submit/disabled/loading states (`Button`, `Spinner`, `Banner`).
- **DataTable** — generic table that infers columns from result-object keys
  (mirrors Streamlit's DataFrame dump; result sets are bounded by `limit ≤ 200/500`,
  so no virtualization). Optional explicit column config where the shape is typed.
- **GraphCanvas** — Cytoscape wrapper (see §7).
- **JobProgress** — poll-driven ingestion job status + result rendering.
- **ToolTrace** — collapsible agent tool-call trace (tool, arguments, error,
  result_count).

Screen specifics:

- **Agent** (`/agent`): message list + chat input + clear button; one
  `POST /agent/query` per send carrying the full message history; renders user /
  assistant bubbles, a collapsible `ToolTrace`, and a truncation `Banner`.
- **Ingestion** (`/ingestion`): migrations panel (status + apply, with `409`
  busy handling), multi-CSV upload + optional `since` + "run resolution after"
  checkbox, a separate resolve button; on submit, poll `GET /ingestion/jobs/{id}`
  via React-Query `refetchInterval` (stops on `done` / `error`), then render
  `counts` / `dropped` / `filtered` / `skipped` / `resolution` tables. Accepted
  filenames: `<table>.csv` / `*_<table>.csv` for
  `postings|comments|messages|profiles|connections`.
- **5 table tools**: typed form → `POST /tools/{name}` → `DataTable`, plus:
  `author_activity_summary` renders per-author metrics + a `top_topics`
  sub-table; `authors_connected_by_topic` groups results per seed author.

## 7. Graph visualization (Cytoscape)

`<GraphCanvas elements layout legend />` wraps `cytoscape` core. Lib-agnostic
mappers (`lib/networkElements.ts`, `lib/socialElements.ts`) convert the tool
JSON `{nodes, edges}` into Cytoscape elements; a stylesheet re-creates the
current DOT semantics:

- **network_around** (bipartite author↔topic): authors = violet-accent rounded
  rectangles, topics = green ellipses, **seed** = amber fill + thick border;
  edge width scales with `weight`. Layout: `cose` (force-directed, built in).
- **social_network_around** (ego network): all rounded rectangles colored by
  `ring` (seed amber → ring 1 accent → ring 2 gray → beyond muted); `follows`
  edges = directed arrow, `friends` edges = undirected dashed. Layout:
  `concentric` keyed by `ring` (seed centered).

Interactivity beyond the old static Graphviz render: pan / zoom, node drag,
click-to-highlight a node's neighborhood, a "fit" control, a legend, and the
existing "capped view" truncation banner. Built-in layouts only (no Cytoscape
extension packages → fewer airgap dependencies).

## 8. Internationalization (backend-driven, no toggle)

New `GET /config` (unauthenticated, like `/health`) returns
`{language, ingestion_enabled, version?}` sourced from `RESPONSE_LANGUAGE` and
`INGESTION_UI_ENABLED`. The SPA carries a typed string catalog (`en` / `de`,
porting the ~160 keys from `ui_strings.py`) exposed via a `useT()` hook;
`ConfigProvider` sets the active language at boot from `/config`. No UI toggle —
this keeps the UI chrome language consistent with the backend-driven agent
answer language (the backend's `RESPONSE_LANGUAGE` also governs agent responses
and entity article-stripping). A unit test asserts EN/DE key parity.

## 9. Backend changes (minimal)

- Add `chorus/api/routers/config.py` (`GET /config`) and register it in
  `main.py`; add a backend test.
- Keep `/ingestion/feature` for backward-compat (the SPA reads `/config`
  instead, but existing tests/contract stay intact).
- No static-file mount, no CORS middleware (same-origin via nginx).
- **No auth-model change.** `chorus/api/auth/principal.py` (the trusted-header
  seam) is untouched; the SPA sends no identity header (see §5). `/config` is
  unauthenticated like `/health` so the SPA can bootstrap language before any
  authenticated call; it exposes only a language code and two booleans (no
  sensitive data).
- Everything else — tools registry, agent loop, ingestion routers, audit
  logger — is untouched.

## 10. Docker / infra

- **`Dockerfile.frontend`** (rewritten): builder `FROM node:20-alpine@sha256:…`
  (corepack pnpm@9.12.0, `pnpm install --frozen-lockfile`, `pnpm build`) →
  runtime `FROM nginx:1.27-alpine@sha256:…` serving `dist/` and reverse-proxying
  the API prefixes. **Both base images pinned by digest** (resolved at
  implementation time via `docker manifest inspect`, kept alongside the tag).
  An env-templated `default.conf` injects `client_max_body_size`
  (`CHORUS_CLIENT_MAX_BODY_SIZE`, default `512m`) for the CSV ingestion uploads
  (social-graph dumps can be large), plus the hardened `security-headers.conf`.
- **`compose.yaml`** frontend service: built nginx image, `expose: 80`,
  `depends_on: backend (service_healthy)`, joins `chorus-net`. Pass
  `CHORUS_CLIENT_MAX_BODY_SIZE`; retire `CHORUS_API_URL` / `CHORUS_UI_IDENTITY`;
  ensure the backend dev env carries `CHORUS_DEFAULT_IDENTITY`. `RESPONSE_LANGUAGE`
  / `INGESTION_UI_ENABLED` remain on the **backend** (now surfaced via `/config`).
- **`compose.override.yaml`**: publish `${CHORUS_FRONTEND_HOST_PORT:-8501}:80`.
- **Vite dev proxy** → `http://localhost:8000` (override via
  `CHORUS_BACKEND_ORIGIN`), proxying each API prefix
  (`/health`, `/config`, `/tools`, `/agent`, `/ingestion`).
- **Makefile**: `build` / `up` / `up-dev` / `bundle` keep working; add
  `frontend-lint` / `frontend-test` convenience targets. `make bundle` now ships
  the nginx-based `chorus-frontend` image tarball.
- An nginx-config test mirrors docint's `tests/test_frontend_proxy_config.py`
  (asserts the digest pins, the templated upload limit, and the SPA fallback).

## 11. Testing & CI

- **Vitest units**: API client + `ApiError`; hooks (incl. job-polling
  stop-on-terminal); i18n EN/DE key parity; the DOT→Cytoscape element mappers;
  `DataTable` column inference; agent `ToolTrace`.
- `pnpm lint` + `pnpm typecheck` clean.
- **CI**: the Docker build already runs `pnpm build` (catches type/build
  errors). Add a frontend job (lint / typecheck / test) to `ci.yml` if the
  shared `nos-tromo/.github` workflow doesn't already cover the frontend —
  to verify during planning.

## 12. Cleanup & docs

- Delete `chorus/chorus/ui/` and its Python tests (`tests/ui/…`, incl.
  `test_frontend_image_surface.py`); remove the streamlit `frontend` dependency
  group from `pyproject.toml` + `uv.lock`.
- Update `CLAUDE.md`: Tech-stack "Streamlit" → "React SPA"; repo-layout `ui/`
  → `frontend/`; the "Adding a graph tool" six-files note (step 5 referenced a
  Streamlit page) → the React screen equivalent.
- Update `README.md` quick start and `docs/architecture.md`.
- Write the ADR (React SPA over Streamlit; Cytoscape for graphs; the `/config`
  endpoint; digest-pinned frontend base images; **the headerless trusted-header
  auth model — matching docint, explicitly rejecting Nextext's client-minted
  owner — and why, for §76/Art. 9 audit integrity**).

## 13. Risks / to verify during planning

- **Graph styling parity** — re-implementing the DOT color/shape/width rules in
  a Cytoscape stylesheet; visually diff against current screenshots.
- **CI frontend job** — confirm whether the shared workflow lints/tests the
  frontend or a dedicated job must be added.
- **Loose-dict result shapes** — confirm the exact keys for `cooccurring` /
  `authors` / `connected` / `top_topics` (generic `DataTable` covers them, but
  pin the columns where known).
- **Python tests importing `chorus.ui`** — find and remove/update them.
- **Base-image digests** — resolve current `node:20-alpine` /
  `nginx:1.27-alpine` digests and document the tag they correspond to.

## 14. Out of scope

New graph tools · SSE streaming (jobs stay polled) · semantic search · auth
model changes (trusted-header seam unchanged) · runtime language toggle ·
changes to translator (its digests are handled by the maintainer).
