# Chorus React SPA Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace chorus's Streamlit UI with a React SPA in `chorus/frontend/`, at full feature parity, consuming the shared `@infra/ui` design system and following the docint/Nextext/translator pattern.

**Architecture:** A Vite-built React 19 SPA served by nginx, which also reverse-proxies the backend's root API paths (same-origin, no CORS). Server state via TanStack Query; routing via react-router. The two network-graph screens render with Cytoscape. A new unauthenticated `GET /config` endpoint feeds the SPA its language + ingestion flag. The backend's trusted-header auth seam is unchanged: the SPA sends no identity header (matching docint).

**Tech Stack:** Node 20 · pnpm 9.12.0 · React 19.2 · Vite 8 · TypeScript 6 (strict) · Tailwind v4 · `@infra/ui#v0.1.1` · `@tanstack/react-query` 5 · `react-router-dom` 7 · `cytoscape` (core) · `@fontsource/inter` · Vitest 4 + happy-dom. Backend: FastAPI (Python 3.12, `uv`).

## Global Constraints

*Every task implicitly includes these. Exact values copied from the spec.*

- **Versions:** Node 20, pnpm `9.12.0` (`packageManager` field), React `^19.2.0`, Vite `^8.0.16`, TypeScript `^6.0.3` (strict), Tailwind `^4.3.1` (`@tailwindcss/postcss`), `@infra/ui` = `github:nos-tromo/infra-ui#v0.1.1`, `@tanstack/react-query` `^5.100.14`, `react-router-dom` `^7.17.0`, `@fontsource/inter` `^5.1.0`, Vitest `^4.1.8`.
- **Accent:** `:root { --app-accent: hsl(262 83% 58%) }` (violet).
- **Airgap:** `pnpm install --frozen-lockfile`; no runtime network fetches; fonts via `@fontsource` (no CDN); Cytoscape is pure JS bundled into `dist/`. The image builds on internet-side CI and bakes `dist/` into nginx.
- **Supply chain:** every `FROM` in chorus's Dockerfiles pinned by `@sha256:` digest (chorus-only; do not touch translator).
- **Auth (load-bearing):** the SPA sends **no** identity header — copy docint's headerless `api/client.ts`, NOT Nextext's `identity/owner.ts`. `chorus/api/auth/principal.py` is untouched. Prod: the OIDC proxy injects `X-Auth-User`; dev: backend `CHORUS_DEFAULT_IDENTITY=dev`.
- **API shape:** backend routes stay at **root** (no `/api` prefix). The SPA calls relative paths; Vite (dev) and nginx (prod) proxy the prefixes `/health`, `/config`, `/tools`, `/agent`, `/ingestion` to the backend. No CORS.
- **No scope creep:** no new tools, no SSE (jobs are polled), no semantic search, no auth-model change, no runtime language toggle.
- **Discipline:** TDD (test first, watch it fail, minimal impl, watch it pass), frequent commits, DRY, YAGNI.

## Reference files (copy/adapt; do not re-derive)

- Headerless API client + query client: `docint/frontend/src/api/client.ts`, `queryClient.ts`.
- App shell / routing: `docint/frontend/src/layout/Shell.tsx`, `Sidebar.tsx`, `routes/Router.tsx`, `main.tsx`, `App.tsx`.
- Boilerplate to copy verbatim then rename: `translator/frontend/{tsconfig.json,tsconfig.node.json,eslint.config.js,postcss.config.js,vite-env.d.ts,test/setup.ts}`.
- Dockerfile/nginx baseline: `translator/docker/Dockerfile.frontend`, `translator/frontend/nginx/*`, plus docint's env-templated `default.conf.template` + hardened `security-headers.conf`.
- Job-polling-via-Query reference (adapt to chorus's *polling*, not SSE): `Nextext/frontend/src/hooks/useJobs.ts`.
- Source-of-truth for screen behavior: the current `chorus/chorus/ui/pages/*.py`, `client.py`, `network_dot.py`, `social_network_dot.py`, `utils/ui_strings.py`.

---

## Phase 0 — Backend `GET /config`

### Task 1: Add the unauthenticated `GET /config` endpoint

**Files:**
- Create: `chorus/chorus/api/routers/config.py`
- Modify: `chorus/chorus/api/main.py` (import + `include_router`)
- Modify: `tests/conftest.py` (add `"chorus.api.routers.config"` to `_CHORUS_ENV_MODULES`, after the `ingestion` entry)
- Test: `tests/api/test_config.py` (mirror the existing health-router test's `TestClient` setup; find it with `grep -rl "def test.*health" tests/`)

**Interfaces:**
- Produces: `GET /config` → `{"language": "en"|"de", "ingestion_enabled": bool, "version": str}`. Unauthenticated (no `resolve_principal` dependency), like `/health`. Reads env **at request time** via `load_language_env()` / `load_ingestion_ui_env()` so per-test env changes are honored.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_config.py
from fastapi.testclient import TestClient
from chorus.api.main import app


def test_config_reports_language_and_flags(monkeypatch):
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    with TestClient(app) as client:
        resp = client.get("/config")  # no auth header
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "de"
    assert body["ingestion_enabled"] is True
    assert isinstance(body["version"], str) and body["version"]


def test_config_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    with TestClient(app) as client:
        resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "en"
    assert body["ingestion_enabled"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_config.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Write the router**

```python
# chorus/chorus/api/routers/config.py
"""Public client-bootstrap config.

Exposes the handful of deployment facts the SPA needs before it can make any
authenticated call: the active UI language and whether the ingestion UI is on.
Unauthenticated by design (like /health) — it returns only a language code and
two booleans, no sensitive data — and reads env at request time so tests and
hot-reloads see current values.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from chorus import __version__
from chorus.utils.env_cfg import load_ingestion_ui_env, load_language_env

router = APIRouter(tags=["config"])


class ConfigOut(BaseModel):
    """Client bootstrap config."""

    language: str
    ingestion_enabled: bool
    version: str


@router.get("/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    """Return the SPA's bootstrap config (language, ingestion flag, version)."""
    return ConfigOut(
        language=load_language_env().code,
        ingestion_enabled=load_ingestion_ui_env().enabled,
        version=__version__,
    )
```

- [ ] **Step 4: Register the router**

In `chorus/chorus/api/main.py`, add the import alongside the other router imports and register it next to `_health_router`:

```python
from chorus.api.routers import config as _config_router
...
app.include_router(_config_router.router)
```

- [ ] **Step 5: Add the module to the conftest reload list**

In `tests/conftest.py`, add `"chorus.api.routers.config",` to `_CHORUS_ENV_MODULES` immediately after `"chorus.api.routers.ingestion",`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_config.py -v`
Expected: PASS (both).

- [ ] **Step 7: Lint/type/commit**

```bash
uv run ruff check chorus/api/routers/config.py && uv run mypy chorus/api/routers/config.py
git add chorus/chorus/api/routers/config.py chorus/chorus/api/main.py tests/conftest.py tests/api/test_config.py
git commit -m "feat(api): add unauthenticated GET /config for SPA bootstrap"
```

---

## Phase 1 — Frontend foundation

### Task 2: Scaffold the building React skeleton

**Files (all under `chorus/frontend/`):**
- Create: `package.json`, `index.html`, `vite.config.ts`, `tsconfig.json`, `tsconfig.node.json`, `eslint.config.js`, `postcss.config.js`, `vitest.config.ts`, `.gitignore`
- Create: `src/main.tsx`, `src/App.tsx`, `src/vite-env.d.ts`, `src/styles/globals.css`, `src/test/setup.ts`
- Generated: `pnpm-lock.yaml` (by `pnpm install`)

**Interfaces:**
- Produces: a project where `pnpm install`, `pnpm typecheck`, `pnpm build`, `pnpm test`, `pnpm lint` all succeed. `App` renders a single "chorus" heading (replaced in Task 8).

- [ ] **Step 1: Copy boilerplate verbatim from translator, then adapt**

Copy these from `translator/frontend/` unchanged: `tsconfig.json`, `tsconfig.node.json`, `eslint.config.js`, `postcss.config.js`, `src/vite-env.d.ts`, `src/test/setup.ts`.

- [ ] **Step 2: Write `package.json`**

```json
{
  "name": "chorus-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "packageManager": "pnpm@9.12.0",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@fontsource/inter": "^5.1.0",
    "@infra/ui": "github:nos-tromo/infra-ui#v0.1.1",
    "@tanstack/react-query": "^5.100.14",
    "cytoscape": "^3.30.0",
    "react": "^19.2.0",
    "react-dom": "^19.2.0",
    "react-router-dom": "^7.17.0"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4.3.1",
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@types/cytoscape": "^3.21.0",
    "@types/react": "^19.2.0",
    "@types/react-dom": "^19.2.0",
    "@vitejs/plugin-react": "^6.0.2",
    "eslint": "^9.12.0",
    "happy-dom": "^15.0.0",
    "tailwindcss": "^4.3.1",
    "typescript": "^6.0.3",
    "vite": "^8.0.16",
    "vitest": "^4.1.8"
  }
}
```

- [ ] **Step 3: Write `vite.config.ts`** (proxy the root API prefixes to the backend)

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const BACKEND = process.env.CHORUS_BACKEND_ORIGIN ?? 'http://localhost:8000'
const proxy = Object.fromEntries(
  ['/health', '/config', '/tools', '/agent', '/ingestion'].map((p) => [
    p,
    { target: BACKEND, changeOrigin: true },
  ]),
)

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': '/src' } },
  server: { port: 5173, strictPort: true, proxy },
  test: { environment: 'happy-dom', globals: true, setupFiles: ['./src/test/setup.ts'] },
})
```

- [ ] **Step 4: Write `index.html`, `src/styles/globals.css`, `src/main.tsx`, `src/App.tsx`**

`index.html` — copy translator's, change `<title>` to `chorus`.

`src/styles/globals.css` (the `@infra/ui` `@theme` tokens are already full `hsl(...)` values and Tailwind v4 generates `bg-*`/`text-*` utilities from them, so use the utilities — never `hsl(var(--token))`):
```css
@import 'tailwindcss';
@import '@infra/ui/theme.css';
@source '../node_modules/@infra/ui/dist';

:root { --app-accent: hsl(262 83% 58%); }

html, body, #root { height: 100%; }
body { @apply bg-background text-foreground; }
```
> Match `docint/frontend/src/styles/globals.css` for any extra rules it sets (font unification, scrollbar, `* { @apply border-border }`).

`src/main.tsx`:
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import './styles/globals.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

`src/App.tsx` (placeholder; replaced in Task 8):
```tsx
export function App() {
  return <h1 className="p-8 text-2xl font-semibold">chorus</h1>
}
```

- [ ] **Step 5: Install, then verify the toolchain**

```bash
cd chorus/frontend
pnpm install
pnpm typecheck && pnpm build && pnpm test && pnpm lint
```
Expected: install writes `pnpm-lock.yaml`; typecheck/build succeed; `vitest` reports "no test files" (ok); lint clean.

- [ ] **Step 6: Commit**

```bash
git add chorus/frontend
git commit -m "build(frontend): scaffold React 19 + Vite + Tailwind v4 + @infra/ui skeleton"
```

### Task 3: API client (`apiGet`/`apiPost`, `ApiError`)

**Files:**
- Create: `chorus/frontend/src/api/client.ts`
- Test: `chorus/frontend/src/api/client.test.ts`

**Interfaces:**
- Produces: `ApiError {status, detail}`; `apiGet<T>(path, params?, signal?): Promise<T>`; `apiPost<T>(path, body?, signal?): Promise<T>` where `body` may be `FormData` (multipart) or a JSON-serializable value. **No identity headers** (docint model). `BASE = import.meta.env.VITE_API_BASE_URL ?? ''` (same-origin default).

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it, vi, afterEach } from 'vitest'
import { ApiError, apiGet, apiPost } from './client'

afterEach(() => vi.restoreAllMocks())

describe('api client', () => {
  it('GET parses JSON and sends no identity header', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: 1 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const out = await apiGet<{ ok: number }>('/health')
    expect(out).toEqual({ ok: 1 })
    const headers = (fetchMock.mock.calls[0][1]?.headers ?? {}) as Record<string, string>
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain('x-auth-user')
  })

  it('throws ApiError with detail on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'nope' }), { status: 422 })))
    await expect(apiGet('/tools/x')).rejects.toMatchObject({ status: 422, detail: 'nope' })
  })

  it('POST sends FormData without forcing content-type', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    await apiPost('/ingestion/ingest', new FormData())
    const init = fetchMock.mock.calls[0][1]!
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.headers as Record<string, string>)['content-type']).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `pnpm test src/api/client.test.ts` → FAIL (module missing).

- [ ] **Step 3: Implement** (adapt `docint/frontend/src/api/client.ts`)

```ts
const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: unknown) {
    super(`API ${status}`)
    this.name = 'ApiError'
  }
}

export function url(path: string): string {
  return `${BASE}${path}`
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text()
  let body: unknown = text
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    /* keep raw text */
  }
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? (body as { detail: unknown }).detail
        : body
    throw new ApiError(res.status, detail)
  }
  return body as T
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined | null>,
  signal?: AbortSignal,
): Promise<T> {
  const qs = params
    ? '?' +
      new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== '')
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  return handle<T>(await fetch(url(path + qs), { signal }))
}

export async function apiPost<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {}
  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    headers['content-type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  return handle<T>(await fetch(url(path), { method: 'POST', headers, body: payload, signal }))
}
```

- [ ] **Step 4: Run to verify pass** — `pnpm test src/api/client.test.ts` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(frontend): headerless typed API client (docint model)"`

### Task 4: Query client + shared API types

**Files:**
- Create: `chorus/frontend/src/api/queryClient.ts` (copy docint's `retryPolicy` + `QueryClient`)
- Create: `chorus/frontend/src/api/types.ts`
- Test: `chorus/frontend/src/api/queryClient.test.ts` (assert `retryPolicy` returns false for a 422 `ApiError`, true once for a 500)

**Interfaces:**
- Produces: `queryClient`; `retryPolicy(failureCount, error): boolean`. Types: `AppConfig`, `ToolMeta`, `ToolsList`, `AgentMessage`, `AgentTraceEntry`, `AgentResponse`, `JobStatus`, `JobKind`, `JobSnapshot`, `MigrationsStatus`. Copy field names verbatim from the endpoint contract in the spec §5 and the inventory.

```ts
// api/types.ts (excerpt — fill the rest from the spec's endpoint table)
export interface AppConfig { language: 'en' | 'de'; ingestion_enabled: boolean; version: string }
export interface ToolMeta { name: string; description: string; input_schema: unknown; output_schema: unknown }
export type ToolsList = ToolMeta[]
export interface AgentMessage { role: 'user' | 'assistant'; content: string }
export interface AgentTraceEntry { tool: string; arguments: Record<string, unknown>; error: string | null; result_count: number | null }
export interface AgentResponse { answer: string; trace: AgentTraceEntry[]; truncated: boolean }
export type JobKind = 'ingest' | 'resolve'
export type JobStatus = 'queued' | 'running' | 'done' | 'error'
export interface JobSnapshot { id: string; kind: JobKind; status: JobStatus; result: Record<string, unknown> | null; error: string | null }
export interface MigrationsStatus { applied: string[]; pending: string[] }
```

- [ ] Steps: test `retryPolicy` → fail → implement (copy docint) → pass → commit (`feat(frontend): query client + API types`).

### Task 5: i18n catalog + `useT`

**Files:**
- Create: `chorus/frontend/src/i18n/en.ts`, `de.ts`, `index.ts`
- Test: `chorus/frontend/src/i18n/i18n.test.ts`

**Interfaces:**
- Produces: `type Lang = 'en' | 'de'`; `type Strings = typeof en` (every key required in both); `catalogs: Record<Lang, Strings>`; `format(template, vars)` for `{placeholder}` interpolation. (The `useT()` React hook lands in Task 6 once `ConfigProvider` exists.)

- [ ] **Step 1: Failing test — EN/DE key parity + interpolation**

```ts
import { describe, expect, it } from 'vitest'
import { en } from './en'
import { de } from './de'
import { format } from './index'

describe('i18n', () => {
  it('en and de have identical key sets', () => {
    expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort())
  })
  it('interpolates named placeholders', () => {
    expect(format('{n} hits', { n: 3 })).toBe('3 hits')
  })
})
```

- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Port every key from `chorus/chorus/utils/ui_strings.py` into `en.ts` (flat `as const` object using the same dotted keys, e.g. `'posts.title'`) and the German equivalents into `de.ts` (typed `: Strings`, forcing parity at compile time). `index.ts` exports `format` and the `catalogs` map:

```ts
// i18n/index.ts
import { en } from './en'
import { de } from './de'
export type Lang = 'en' | 'de'
export type Strings = typeof en
export const catalogs: Record<Lang, Strings> = { en, de }
export function format(template: string, vars: Record<string, string | number> = {}): string {
  return template.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? String(vars[k]) : `{${k}}`))
}
```
> Source the EN/DE text directly from `ui_strings.py` so wording matches today exactly. ~160 keys — port them all; the parity test fails if any is missing.

- [ ] **Step 4:** run → PASS. **Step 5:** commit (`feat(frontend): i18n catalog ported from ui_strings (en/de parity test)`).

### Task 6: `ConfigProvider` + `useConfig` + `useT`

**Files:**
- Create: `chorus/frontend/src/api/config.ts` (`fetchConfig`), `src/config/ConfigContext.tsx`
- Test: `chorus/frontend/src/config/ConfigContext.test.tsx`

**Interfaces:**
- Consumes: `apiGet` (Task 3), `AppConfig` (Task 4), `catalogs`/`format`/`Lang` (Task 5).
- Produces: `<ConfigProvider>` (fetches `/config` once via React Query; renders a `Spinner` while loading and a `Banner` on error); `useConfig(): AppConfig`; `useT(): (key: keyof Strings, vars?) => string` (looks up `catalogs[config.language][key]`, applies `format`).

- [ ] **Step 1: Failing test** — render a probe inside `<QueryClientProvider><ConfigProvider>` with `apiGet` mocked to return `{language:'de', ingestion_enabled:true, version:'0.1.0'}`; assert a German string from a known key renders and `useConfig().ingestion_enabled` is true.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** `fetchConfig = () => apiGet<AppConfig>('/config')`; provider uses `useQuery({queryKey:['config'], queryFn: fetchConfig, staleTime: Infinity})`; context exposes config; `useT` closes over `config.language`.
- [ ] **Step 4:** run → PASS. **Step 5:** commit (`feat(frontend): ConfigProvider/useConfig/useT bootstrapped from /config`).

### Task 7: App shell — `Shell` + `Sidebar`

**Files:**
- Create: `chorus/frontend/src/layout/Shell.tsx`, `src/layout/Sidebar.tsx`
- Test: `chorus/frontend/src/layout/Sidebar.test.tsx`

**Interfaces:**
- Consumes: `useConfig` (for `ingestion_enabled`), `useT`, `react-router-dom` `NavLink`.
- Produces: `<Shell>{children}</Shell>` (sidebar + scrollable `<main>`, adapt `docint/frontend/src/layout/Shell.tsx`); `<Sidebar>` with grouped `NavLink`s. The **Ingestion** link renders only when `useConfig().ingestion_enabled`.

Sidebar nav model (label keys from i18n; paths from spec §4):
```
Agent            → /agent
Entities  · Posts mentioning      → /tools/posts-mentioning
          · Authors mentioning    → /tools/authors-mentioning
Authors   · Activity summary      → /tools/author-activity
          · Connected by topic    → /tools/authors-connected
Topics    · Co-occurrence         → /tools/topic-cooccurrence
Networks  · Network around        → /tools/network-around
          · Social network around → /tools/social-network-around
Ingestion (conditional)           → /ingestion
```

- [ ] Steps: test (ingestion link hidden when flag false, shown when true; active-link styling via `NavLink`) → fail → implement → pass → commit (`feat(frontend): Shell + grouped Sidebar with conditional ingestion nav`).

### Task 8: Router + `App` wiring

**Files:**
- Modify: `chorus/frontend/src/App.tsx`
- Create: `chorus/frontend/src/routes/Router.tsx`, and placeholder screen components `src/routes/{Landing,Agent,Ingestion,ToolPosts,ToolAuthorsMentioning,ToolAuthorActivity,ToolAuthorsConnected,ToolTopicCooc,ToolNetwork,ToolSocial}.tsx` (each a one-line stub returning its name)
- Test: `chorus/frontend/src/routes/Router.test.tsx`

**Interfaces:**
- Produces: `App` = `QueryClientProvider` → `ConfigProvider` → `BrowserRouter` → `Shell` → `<Routes>` mapping spec §4 paths to screens. Stubs are replaced in Phase 3.

```tsx
// App.tsx
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { queryClient } from './api/queryClient'
import { ConfigProvider } from './config/ConfigContext'
import { Shell } from './layout/Shell'
import { AppRoutes } from './routes/Router'

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider>
        <BrowserRouter>
          <Shell>
            <AppRoutes />
          </Shell>
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
```

- [ ] Steps: test (render `App` with `apiGet` mocked for `/config`; navigate to `/agent`, assert stub shows; unknown path redirects to `/`) → fail → implement Router with a catch-all `<Navigate to="/" />` → pass → commit (`feat(frontend): router + screen stubs + app providers wired`).

---

## Phase 2 — Shared components & core hooks

### Task 9: Form controls + `useToolForm`

**Files:**
- Create: `src/components/form/{EntityInput,TimeRangeInputs,LimitField,SubmitButton}.tsx`, `src/components/form/useToolForm.ts`
- Test: `src/components/form/form.test.tsx`

**Interfaces:**
- Produces: controlled inputs built on `@infra/ui` `Input`/`Select`/`Button`. `TimeRangeInputs` → `{ from?: string; to?: string }` (ISO text, optional). `LimitField({min,max,value,onChange})`. `useToolForm<T>(initial)` → `{ values, set, reset }`. `SubmitButton({ loading, disabled, children })` shows `Spinner` when loading.

- [ ] Steps: test (typing updates value; empty required entity disables submit; `LimitField` clamps to `[min,max]`) → fail → implement → pass → commit.

### Task 10: `DataTable` (key-inferred + typed columns)

**Files:**
- Create: `src/components/DataTable.tsx`
- Test: `src/components/DataTable.test.tsx`

**Interfaces:**
- Produces: `DataTable<T extends Record<string, unknown>>({ rows, columns?, empty })`. With no `columns`, infer them from the union of keys across `rows` (stable first-seen order), rendering scalar cells as text and objects/arrays as compact JSON (mirrors Streamlit's DataFrame dump). With `columns`, render `{key,label,render?}` defs. Renders `empty` (a localized string) when `rows` is empty.

- [ ] Steps: test (infers columns from mixed-key rows; respects explicit columns; renders empty state) → fail → implement → pass → commit.

### Task 11: Core hooks — `useHealth`, `useTools`, `useToolCall`

**Files:**
- Create: `src/api/tools.ts` (`listTools`, `callTool`), `src/hooks/{useHealth,useTools,useToolCall}.ts`
- Test: `src/hooks/tools.test.tsx`

**Interfaces:**
- Consumes: `apiGet`/`apiPost`, types from Task 4.
- Produces: `useHealth()` → `useQuery(['health'], () => apiGet('/health'))`; `useTools()` → `useQuery(['tools'], listTools)` where `listTools = () => apiGet<ToolsList>('/tools')`; `useToolCall<TOut>(name)` → `useMutation((payload) => callTool<TOut>(name, payload))` where `callTool = (name, payload) => apiPost('/tools/' + name, payload)`.

- [ ] Steps: test with mocked `apiGet`/`apiPost` inside a `QueryClientProvider` wrapper (assert correct paths called, data surfaced) → fail → implement → pass → commit.

---

## Phase 3 — Screens

### Task 12: Landing screen

**Files:** Modify `src/routes/Landing.tsx`; Test `src/routes/Landing.test.tsx`.

**Interfaces:** Consumes `useHealth`, `useTools`, `useConfig`, `useT`. Renders backend health (ok/unreachable `Banner`), the registered-tools list (from `useTools`, names + descriptions), and an ingestion-status line keyed off `useConfig().ingestion_enabled`. Mirrors `streamlit_app.py`.

- [ ] Steps: test (health ok shows tools; health error shows danger banner; ingestion line reflects flag) → fail → implement → pass → commit.

### Task 13: Config-driven `ToolScreen` + the 3 simple table tools

**Files:**
- Create: `src/components/ToolScreen.tsx`, `src/tools/specs.ts`
- Modify: `src/routes/{ToolPosts,ToolAuthorsMentioning,ToolTopicCooc}.tsx`
- Test: `src/components/ToolScreen.test.tsx`, `src/tools/specs.test.ts`

**Interfaces:**
- Produces: `interface ToolSpec { name: string; titleKey; captionKey?; fields: FieldSpec[]; resultKey: string; columns?: ColumnDef[]; emptyKey }` where `FieldSpec` is one of entity/text/limit/timeRange. `<ToolScreen spec={...} />` builds the form via Task 9 controls, calls `useToolCall(spec.name)`, and renders `result[spec.resultKey]` through `DataTable`. The 3 simple screens are one-liners: `<ToolScreen spec={POSTS_MENTIONING} />` etc.

Specs (fields/paths/result keys verbatim from inventory §A.4/A.6/A.7 and §5):
- `POSTS_MENTIONING`: name `posts_mentioning`, fields `entity`(req)+`limit`(1–200,50)+`timeRange`, payload `{entity, limit, from?, to?}`, resultKey `hits`.
- `AUTHORS_MENTIONING`: name `authors_mentioning`, same fields, resultKey `authors`.
- `TOPIC_COOCCURRENCE`: name `topic_co_occurrence`, field `topic`(req)+`limit`+`timeRange`, payload `{topic, limit, from?, to?}`, resultKey `cooccurring`.

> Payload uses keys `from`/`to` (not `from_`); the backend aliases them. Confirm against `chorus/chorus/tools/*.py` input models.

- [ ] Steps: test (`ToolScreen` submits the right payload to the right path and tables the result; one spec object validated) → fail → implement → pass → commit.

### Task 14: `author_activity_summary` screen (bespoke result)

**Files:** Modify `src/routes/ToolAuthorActivity.tsx`; Test alongside.

**Interfaces:** Form: `author`(req) + `timeRange`. Calls `useToolCall('author_activity_summary')`, payload `{author, from?, to?}`. Renders per-summary cards (`{label} · {author_id}` where `label = display_name || handle || author_id`), the activity metrics, and a `top_topics` `DataTable` (or the "no topics" localized line). Mirrors `03_author_activity_summary.py`.

- [ ] Steps: test (multiple summaries render; empty `top_topics` shows the no-topics line) → fail → implement → pass → commit.

### Task 15: `authors_connected_by_topic` screen (per-seed groups)

**Files:** Modify `src/routes/ToolAuthorsConnected.tsx`; Test alongside.

**Interfaces:** Form: `seed_author`(req) + `min_overlap`(number ≥1, default 1) + `limit`(1–200,50). Payload `{seed_author, min_overlap, limit}`, resultKey `results`; each group renders a `{label} · {n} connected` header + a `connected` `DataTable` (or the localized "none" line). Mirrors `05_authors_connected_by_topic.py`.

- [ ] Steps: test → fail → implement → pass → commit.

### Task 16: Agent chat screen

**Files:**
- Create: `src/api/agent.ts` (`agentQuery`), `src/hooks/useAgentQuery.ts`, `src/components/ToolTrace.tsx`
- Modify: `src/routes/Agent.tsx`
- Test: `src/routes/Agent.test.tsx`, `src/components/ToolTrace.test.tsx`

**Interfaces:**
- Produces: `agentQuery = (messages) => apiPost<AgentResponse>('/agent/query', { messages })`; `useAgentQuery()` mutation; `<ToolTrace trace={...} />` collapsible (`tool`, `arguments`, `error`, `result_count`). `Agent` keeps `messages` in React state, appends user turn, sends full history, appends assistant turn, renders bubbles + trace + a truncation `Banner` when `truncated`; a "clear" button resets state. Mirrors `00_agent.py` (non-streaming).

- [ ] Steps: test (sends accumulated history; renders assistant answer + trace; clear empties; truncated shows banner) → fail → implement → pass → commit.

### Task 17: Graph element mappers (pure functions — TDD core)

**Files:**
- Create: `src/lib/networkElements.ts`, `src/lib/socialElements.ts`
- Test: `src/lib/networkElements.test.ts`, `src/lib/socialElements.test.ts`

**Interfaces:**
- Produces:
  - `toNetworkElements(out: NetworkAroundOut): { elements: cytoscape.ElementDefinition[]; }` mapping nodes→`{ data:{id,label,kind,isSeed}, classes }` and edges→`{ data:{id:source+'__'+target, source, target, weight} }`. Class rules from `network_dot.py`: `author` (violet rect), `topic` (green ellipse), `seed` overrides. Edge `width` data drives the stylesheet (penwidth rule `1 + 0.5*max(weight-1,0)` capped at 6).
  - `toSocialElements(out: SocialNetworkAroundOut): {...}` mapping nodes by `ring` class (`seed`/`ring1`/`ring2`/`ringN`) and edges with `kind` (`follows`/`friends`) + `directed`. Rules from `social_network_dot.py`.
- Define the `NetworkAroundOut`/`SocialNetworkAroundOut` types here (or in `api/types.ts`) using node/edge fields from inventory §D.6/§D.7.

- [ ] Steps: test (seed node gets `seed` class; author vs topic class; edge id is stable + dedupes; follows vs friends + directed flag; ring bucketing incl. ring≥3 → `ringN`) → fail → implement → pass → commit. *These are the highest-risk parity logic — test them hard.*

### Task 18: `GraphCanvas` + the two graph screens

**Files:**
- Create: `src/components/GraphCanvas.tsx`, `src/lib/graphStyles.ts`
- Modify: `src/routes/ToolNetwork.tsx`, `src/routes/ToolSocial.tsx`
- Test: `src/components/GraphCanvas.test.tsx`

**Interfaces:**
- Consumes: `cytoscape` core, mappers (Task 17), `useToolCall`.
- Produces: `<GraphCanvas elements layout stylesheet />` — a thin wrapper that inits `cytoscape({ container, elements, style, layout })` in a `useEffect` on a `ref` div, re-runs layout on `elements` change, destroys on unmount, exposes a "fit" button and click-to-highlight-neighborhood. `graphStyles.ts` exports the two stylesheets implementing §7 (violet authors / green topics / amber seed / weighted edges; ring colors; `target-arrow-shape:triangle` for directed, `line-style:dashed` + no arrow for friends). `ToolNetwork` uses layout `cose`; `ToolSocial` uses `concentric` keyed by ring. Both render node/edge counts and the "capped view" `Banner` when `truncated`.

> `GraphCanvas` test runs under happy-dom: cytoscape needs a container with size; assert it mounts and `cy.elements().length` equals the mapped count rather than asserting pixels. Mock `cytoscape` if happy-dom can't lay out — assert the wrapper passes the right `elements`/`style`/`layout` into the constructor.

- [ ] Steps: test (constructor receives mapped elements + chosen layout; fit button calls `cy.fit`; truncated banner) → fail → implement → pass → commit.

### Task 19: Ingestion API + hooks (polling)

**Files:**
- Create: `src/api/ingestion.ts`, `src/hooks/{useMigrations,useIngest,useResolve,useJob}.ts`
- Test: `src/hooks/ingestion.test.tsx`

**Interfaces:**
- Produces:
  - `getMigrations = () => apiGet<MigrationsStatus>('/ingestion/migrations')`; `applyMigrations = () => apiPost<{applied:string[]}>('/ingestion/migrate')`.
  - `startIngest = (files: File[], since?: string, thenResolve=false) => apiPost<JobSnapshot>('/ingestion/ingest', form)` building `FormData` with repeated `files`, `since`, `then_resolve`.
  - `startResolve = () => apiPost<JobSnapshot>('/ingestion/resolve')`.
  - `useJob(jobId | null)` → `useQuery({ queryKey:['job',jobId], queryFn:()=>apiGet<JobSnapshot>('/ingestion/jobs/'+jobId), enabled: !!jobId, refetchInterval: (q) => isTerminal(q.state.data?.status) ? false : 1500 })` where `isTerminal = s => s==='done' || s==='error'`. On terminal, invalidate `['ingestion','migrations']`.
  - `useMigrations`/`useIngest`/`useResolve` wrap the above; mutations surface `409` (busy) cleanly via `ApiError.status`.

- [ ] Steps: test (`useJob` stops polling once status is `done`; `startIngest` builds FormData with all files + flags; `409` surfaces as ApiError) → fail → implement → pass → commit. *Reference `Nextext/frontend/src/hooks/useJobs.ts` for the Query shape, but use `refetchInterval` polling, not SSE.*

### Task 20: Ingestion screen UI

**Files:** Modify `src/routes/Ingestion.tsx`; Test `src/routes/Ingestion.test.tsx`.

**Interfaces:** Consumes Task 19 hooks + `useT`. Three sections (mirror `01_data_ingestion.py`): (1) migrations status + "apply" (disabled while a job is active); (2) multi-CSV file picker + optional `since` + "run resolution after" checkbox + "start ingestion" (disabled if busy or no files); (3) "run resolution" button. On submit, track `jobId` in state, drive `JobProgress` via `useJob`, and on `done` render `counts`/`dropped`/`filtered`/`skipped`/`resolution` tables; on `error` render the error `Banner`. Accept only `.csv`; the screen itself is reachable only when `ingestion_enabled` (route still renders a "disabled" notice if hit directly).

- [ ] Steps: test (upload→poll→render counts on done; busy disables controls; error banner on error status) → fail → implement → pass → commit.

---

## Phase 4 — Docker / infra

### Task 21: nginx config (templated) + security headers

**Files:**
- Create: `chorus/frontend/nginx/default.conf.template`, `chorus/frontend/nginx/security-headers.conf`

**Interfaces:** `default.conf.template` proxies each API prefix (`/health`, `/config`, `/tools`, `/agent`, `/ingestion`) to `http://backend:8000$request_uri`; sets `client_max_body_size ${CHORUS_CLIENT_MAX_BODY_SIZE};` on the `/ingestion/` location; caches `/assets/` immutably for 1y; SPA-fallback `try_files $uri /index.html` (no-cache). `security-headers.conf` = docint's hardened CSP/permissions-policy/nosniff/DENY. The frontend nginx forwards the incoming `X-Auth-User` unchanged (trust boundary is the outer OIDC proxy; the SPA never sets it).

- [ ] Steps: write both files (adapt docint's). No unit test here — covered by the Python config test in Task 22. Commit (`feat(frontend): nginx SPA serving + API proxy + hardened headers`).

### Task 22: `Dockerfile.frontend` (digest-pinned) + Python guard test

**Files:**
- Rewrite: `chorus/docker/Dockerfile.frontend`
- Create: `tests/test_frontend_proxy_config.py` (mirror docint's)

**Interfaces:** node→nginx multi-stage; **node and nginx pinned by `@sha256:`**; env-templated upload limit (`ENV CHORUS_CLIENT_MAX_BODY_SIZE=512m`, `default.conf.template` → `/etc/nginx/templates/`).

- [ ] **Step 1: Resolve the current digests** (record the tag they map to in a comment)

```bash
docker manifest inspect docker.io/library/node:20-alpine   | sed -n 's/.*"digest": "\(sha256:[0-9a-f]*\)".*/\1/p' | head -1
docker manifest inspect docker.io/library/nginx:1.27-alpine | sed -n 's/.*"digest": "\(sha256:[0-9a-f]*\)".*/\1/p' | head -1
```
(If `docker` is unavailable, use `crane digest docker.io/library/node:20-alpine`.)

- [ ] **Step 2: Write the failing Python test**

```python
# tests/test_frontend_proxy_config.py
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]


def test_frontend_base_images_are_digest_pinned():
    df = (REPO / "docker" / "Dockerfile.frontend").read_text()
    froms = re.findall(r"(?m)^\s*(?:ARG\s+\w+=|FROM\s+)\S*(node|nginx)\S*", df)
    assert froms, "expected node + nginx base images"
    for line in df.splitlines():
        if ("node:" in line or "nginx:" in line) and ("FROM" in line or "ARG" in line):
            assert "@sha256:" in line, f"base image not digest-pinned: {line}"


def test_frontend_templated_upload_limit():
    df = (REPO / "docker" / "Dockerfile.frontend").read_text()
    assert "CHORUS_CLIENT_MAX_BODY_SIZE" in df
    assert "templates/default.conf.template" in df
    conf = (REPO / "frontend" / "nginx" / "default.conf.template").read_text()
    assert "client_max_body_size ${CHORUS_CLIENT_MAX_BODY_SIZE};" in conf


def test_frontend_spa_fallback_and_api_proxy():
    conf = (REPO / "frontend" / "nginx" / "default.conf.template").read_text()
    assert "try_files $uri /index.html" in conf
    for prefix in ("/config", "/tools", "/agent", "/ingestion", "/health"):
        assert prefix in conf
```

- [ ] **Step 3:** run → FAIL. **Step 4: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1
# Pins map to node:20-alpine / nginx:1.27-alpine as of <DATE> (see Task 22).
ARG NODE_IMAGE=docker.io/library/node:20-alpine@sha256:<NODE_DIGEST>
ARG NGINX_IMAGE=docker.io/library/nginx:1.27-alpine@sha256:<NGINX_DIGEST>

FROM ${NODE_IMAGE} AS builder
WORKDIR /build
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM ${NGINX_IMAGE}
ENV CHORUS_CLIENT_MAX_BODY_SIZE=512m
RUN rm /etc/nginx/conf.d/default.conf
COPY frontend/nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /build/dist /usr/share/nginx/html
EXPOSE 80
```

- [ ] **Step 5:** run `uv run pytest tests/test_frontend_proxy_config.py -v` → PASS. **Step 6: Commit** (`build(frontend): node+nginx Dockerfile, digest-pinned, templated upload limit`).

### Task 23: compose + override + Makefile

**Files:**
- Modify: `chorus/docker/compose.yaml` (frontend service), `chorus/docker/compose.override.yaml`, `chorus/Makefile`
- Modify/confirm: backend dev env carries `CHORUS_DEFAULT_IDENTITY` (compose.override or `.env.example`)

**Interfaces:** Frontend service → built nginx image; `expose: ["80"]`; `depends_on: backend: { condition: service_healthy }`; `environment: CHORUS_CLIENT_MAX_BODY_SIZE: ${CHORUS_CLIENT_MAX_BODY_SIZE:-512m}`; **remove** `CHORUS_API_URL`/`CHORUS_UI_IDENTITY`; keep `RESPONSE_LANGUAGE`/`INGESTION_UI_ENABLED` on **backend** only. Override publishes `${CHORUS_FRONTEND_HOST_PORT:-8501}:80` and sets `CHORUS_DEFAULT_IDENTITY: ${CHORUS_DEFAULT_IDENTITY:-dev}` on the **backend** for dev. Makefile: add `frontend-lint`/`frontend-test` (`cd frontend && pnpm install --frozen-lockfile && pnpm lint|test`); `build`/`up`/`up-dev`/`bundle` already cover the new image.

- [ ] Steps: edit files; `make build` (or `docker compose -f docker/compose.yaml config`) to validate compose parses; commit (`build(frontend): nginx compose service, dev port + dev identity, make targets`).

### Task 24: CI frontend job

**Files:** Modify `chorus/.github/workflows/ci.yml` (and/or confirm the shared workflow).

**Interfaces:** Ensure CI runs `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm typecheck`, `pnpm test` for `frontend/`, and that the Docker build (which runs `pnpm build`) stays green. If `nos-tromo/.github`'s shared `python-app-ci` already has a frontend job (check how docint/translator wire it), mirror that; else add a `frontend` job.

- [ ] **Step 1:** `grep -rn "pnpm\|frontend\|node" .github/workflows/ /Users/himarc/dev/nos-tromo/infra/docint/.github/workflows/` to see the existing convention. **Step 2:** add/align the job. **Step 3:** commit (`ci(frontend): lint/typecheck/test the SPA`).

---

## Phase 5 — Cleanup & docs

### Task 25: Remove Streamlit

**Files:**
- Delete: `chorus/chorus/ui/` (entire dir), and the Streamlit Python tests (`tests/ui/`, incl. `test_frontend_image_surface.py` — find all with `grep -rln "chorus.ui\|streamlit" tests/`)
- Modify: `chorus/pyproject.toml` (remove the `frontend` dependency group: streamlit/httpx/python-dotenv), then `uv lock`
- Modify: `tests/conftest.py` (remove any now-dangling `chorus.ui.*` entries from `_CHORUS_ENV_MODULES` if present)

**Interfaces:** After removal, `uv run pytest` is green and no module imports `chorus.ui`.

- [ ] **Step 1:** `grep -rn "chorus.ui\|import streamlit\|streamlit" chorus/ tests/` → enumerate references. **Step 2:** delete `chorus/ui` + Streamlit tests. **Step 3:** remove the `frontend` group from `pyproject.toml`; `uv lock`. **Step 4:** `uv run pytest` → PASS; `uv run ruff check . && uv run mypy .` → clean. **Step 5: Commit** (`refactor: remove Streamlit UI (replaced by React SPA)`).

### Task 26: Docs — CLAUDE.md, README, architecture, ADR 0015

**Files:**
- Modify: `chorus/CLAUDE.md` (Tech stack: Streamlit→React SPA; repo-layout `ui/`→`frontend/`; the "Adding a graph tool" six-files note — step 5 was a Streamlit page, now a React screen + spec); `chorus/README.md` (quick start: `pnpm`/`make up-dev`, new ports); `chorus/docs/architecture.md` (frontend tier)
- Create: `chorus/docs/decisions/0015-react-spa-frontend.md`

**Interfaces:** ADR 0015 records: React SPA over Streamlit (alignment with infra-ui/docint/Nextext); Cytoscape for the network graphs (vs viz.js/server-SVG); the unauthenticated `/config` bootstrap endpoint; digest-pinned frontend base images; **the headerless trusted-header auth model — matches docint, explicitly rejects Nextext's client-minted owner — and why (per-user §76/Art. 9 audit integrity; the SPA fixes Streamlit's fixed-identity gap)**; statelessness (ephemeral single-instance job registry, polled).

- [ ] **Step 1:** write ADR 0015 (use the existing ADRs' format). **Step 2:** update CLAUDE.md/README/architecture. **Step 3:** `uv run pre-commit run --all-files` (catches any doc-adjacent lint). **Step 4: Commit** (`docs: ADR 0015 + CLAUDE/README/architecture for the React SPA`).

---

## Final verification (run before declaring done)

- [ ] `cd chorus/frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build` — all green.
- [ ] `cd chorus && uv run pytest && uv run ruff check . && uv run mypy .` — all green.
- [ ] `make build` builds both images; `make up-dev`, then smoke-test each screen against a seeded stack (per README), including a graph render and an ingestion job (with `INGESTION_UI_ENABLED=true`).
- [ ] `grep -rn "chorus.ui\|streamlit\|CHORUS_UI_IDENTITY" chorus/ tests/ docker/ Makefile` returns nothing.
- [ ] Confirm `node`/`nginx` lines in `Dockerfile.frontend` contain `@sha256:`.
