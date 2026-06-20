# 0015 — React SPA frontend (replaces Streamlit)

Status: accepted
Date: 2026-06-20

## Context

The chorus UI was built with Streamlit — the same choice docint made in its
early prototype. Streamlit was practical for rapid iteration: the Python-backed
server-side model meant the UI could share the FastAPI app's own env-config and
httpx client, and `st.session_state` managed chat history and form state in
process.

Two issues made the Streamlit approach increasingly awkward:

1. **Drift from the infra family.** docint, Nextext, and translator had all
   migrated to Vite + React + TypeScript + Tailwind v4, backed by the shared
   `@infra/ui` design system. Keeping chorus on Streamlit meant maintaining a
   second set of deployment primitives (a Python-based frontend image, its own
   startup env vars, its own `server.maxUploadSize` tuning for CSV uploads, and
   a separate Nginx vhost) while the family converged on a single pattern.

2. **Authentication attribution gap.** Streamlit runs as a server-side process.
   Its httpx client was calling the FastAPI backend with a fixed service identity
   (`CHORUS_UI_IDENTITY`), not with the per-request OIDC identity the upstream
   proxy had injected. That meant the §76 BDSG audit log recorded the UI
   container's identity for every tool invocation, not the user who actually
   triggered it — an audit attribution defect over Art. 9 data.

The React SPA fixes both: it is a static bundle behind nginx that forwards
browser requests through the same upstream OIDC proxy, so the per-user identity
propagates to the backend with no extra wiring.

## Decision

Replace `chorus/ui/` with a React SPA in `frontend/`, removing Streamlit
entirely. The SPA is built on CI (`pnpm install --frozen-lockfile`,
`pnpm build`) and shipped as a digest-pinned nginx image. Five specific
sub-decisions follow.

### 1. React SPA over Streamlit (aligns with the infra family)

The SPA uses Vite 8, React 19, TypeScript 6 (strict), Tailwind v4, and
`@infra/ui#v0.1.1` — the same stack as docint, Nextext, and translator. `pnpm`
is the package manager (matching `packageManager` in each other repo).
`@tanstack/react-query` 5 handles server state; `react-router-dom` 7 handles
routing.

Streamlit's server-side `st.session_state` (chat turns, form inputs) becomes
client-side React state. The backend is completely unchanged; the only new
backend surface is a single config-bootstrap endpoint (§3 below).

The frontend image is an nginx container (`frontend/nginx/default.conf.template`,
`frontend/nginx/security-headers.conf`). The nginx reverse-proxies the exact
API prefixes (`/health`, `/config`, `/tools`, `/agent`, `/ingestion`) to the
backend service, so no CORS is needed and no `/api` prefix is added to any
backend route.

**Why not keep Streamlit?** The Streamlit image ran a Python interpreter,
Tornado, and a WebSocket connection per browser tab. nginx serving a static
bundle is simpler, leaner, and aligns the deployment model with the rest of the
family. The cost was porting the `~160` `ui_strings.py` captions to a typed
TypeScript i18n catalog (`frontend/src/i18n/`), which is verifiable by a unit
test (EN/DE key parity).

### 2. Cytoscape.js for network-graph screens (over viz.js, Graphviz-WASM, or server-rendered SVG)

Two tools (`network_around`, `social_network_around`) return `{nodes, edges}`
payloads. The old UI sent those through `ui/network_dot.py` and
`ui/social_network_dot.py` to Graphviz and rendered a static SVG in Streamlit.

The React SPA wraps `cytoscape` core in a thin `<GraphCanvas>` component
(`frontend/src/components/GraphCanvas.tsx`) with lib-agnostic element mappers
(`frontend/src/lib/networkElements.ts`, `frontend/src/lib/socialElements.ts`).

Alternatives considered:

- **Graphviz-WASM.** Would preserve the exact DOT layout algorithm but adds a
  large WASM binary and is harder to make interactive. The static SVG output is
  a step back in usability compared to what the other infra apps offer.
- **viz.js / `@hpcc-js/wasm`.** Same concern: WASM bundle size, static output.
- **Backend-rendered SVG (serve the DOT string).** Simple to implement but
  leaves the user with a static image they cannot pan, zoom, or click into.
- **react-cytoscapejs.** Wraps `cytoscape` but carries a stale peer-dep on
  React 18; chorus targets React 19. A thin custom wrapper avoids the
  peer-dep and the indirection.

Cytoscape was chosen because: it is pure JS (no WASM, airgap-safe, bundled by
Vite), it supports interactive pan/zoom/drag and click-to-highlight, and its
stylesheet system can faithfully re-create the DOT colour/shape/width semantics
from the old `network_dot.py` files. Built-in layouts only (`cose` for the
bipartite entity–author graph, `concentric` keyed by `ring` for the ego
network) — no Cytoscape extension packages are loaded, keeping the airgap
surface narrow.

### 3. Unauthenticated `GET /config` endpoint bootstraps language and ingestion flag

The SPA is served as static files and cannot read backend env directly. Two
pieces of backend configuration must be visible at boot before any
authenticated call:

- `RESPONSE_LANGUAGE` — controls which i18n catalog the SPA activates.
- `INGESTION_UI_ENABLED` — controls whether the ingestion nav item and route
  are rendered.

A new unauthenticated `GET /config` endpoint (`chorus/api/routers/config.py`,
registered in `main.py`) returns `{language, ingestion_enabled}` (and an
optional `version`). It is intentionally narrow — only a language code and two
booleans, no sensitive data. `ConfigProvider` (`frontend/src/config/`) fetches
it once at app boot and supplies both values via React context.

`RESPONSE_LANGUAGE` and `INGESTION_UI_ENABLED` remain on the **backend** only;
the SPA reads them via `/config`. The existing `/ingestion/feature` endpoint is
kept for backward-compatibility (its contract is covered by tests).

### 4. Headerless trusted-header auth — matches docint, explicitly does NOT copy Nextext

There are two identity models in the nos-tromo family:

- **Trusted-header auth (chorus + docint).** `X-Auth-User` is injected by the
  upstream Nginx/OIDC proxy. `chorus/api/auth/principal.py` reads it and
  falls back to `CHORUS_DEFAULT_IDENTITY` in dev. The SPA sends **no** identity
  header; the browser request transits the OIDC proxy, which sets/overwrites
  the header, and nginx forwards it unchanged to the backend.

- **Client-minted anonymous owner (Nextext).** Nextext's SPA runs against an
  unauthenticated endpoint and mints a random per-browser UUID
  (`identity/owner.ts::resolveOwnerId()`) to give each browser a stable job
  namespace. That design is intentional for Nextext's workload and principal
  model.

Chorus must **not** copy Nextext's `resolveOwnerId()`. Chorus processes
behavioral observation data with a high probability of containing Art. 9
categories. §76 BDSG requires an immutable, per-user audit trail. If the SPA
minted a random identifier and sent it as `X-Auth-User`, any browser could
forge or rotate its own principal and the audit log would record a fabricated
identity — an auth-bypass and an audit-integrity break.

The correct model is the one docint already uses: the SPA's API client
(`frontend/src/api/client.ts`) sends no identity header. The upstream OIDC
proxy is the sole source of truth for `X-Auth-User`. In development,
`CHORUS_DEFAULT_IDENTITY=dev` (set in the backend dev env or `.env`) stands in.

**Bonus:** this is strictly more correct than the Streamlit era. The old
`CHORUS_UI_IDENTITY` was a *fixed* service identity sent on every httpx call
from the Streamlit container — the real OIDC-authenticated user never reached
the §76 audit log. With the SPA, every browser request carries the proxy-set
per-user header directly, so audit attribution is correct from the first request.

Retired env vars: `CHORUS_API_URL`, `CHORUS_UI_IDENTITY`, `CHORUS_UI_TIMEOUT_S`.
Dev alias added to compose: `CHORUS_DEFAULT_IDENTITY=dev`.

### 5. Digest-pinned frontend base images

`docker/Dockerfile.frontend` uses multi-stage build:

- Builder stage: `FROM node:20-alpine@sha256:<digest>` (tag: `node:20.19-alpine`)
  — corepack enables pnpm 9.12.0; `pnpm install --frozen-lockfile`; `pnpm build`.
- Runtime stage: `FROM nginx:1.27-alpine@sha256:<digest>` (tag: `nginx:1.27.5-alpine`)
  — copies `dist/` and the templated nginx config.

Both base images are pinned by `@sha256:` digest, matching the backend's uv
image pin (`uv:0.7.8@sha256:…`). Pinning was not required for the node/nginx
images before because only the backend Dockerfile existed; the frontend
migration is the right moment to harden both tiers together.

Rationale: pinning prevents a mutable tag (`node:20-alpine`) from silently
pulling a different image on the next build — a supply-chain risk in the airgap
delivery model where image tarballs are committed artefacts audited before
deployment.

Airgap invariant: the static bundle is fully materialized at build time. The
nginx runtime fetches nothing; Cytoscape and the `@fontsource/inter` font are
bundled by Vite. No runtime package-manager call occurs on the airgapped side.

## Consequences

- **Full UI parity in one migration.** Every current Streamlit screen is ported:
  Landing, NL Agent, Data Ingestion, and all seven tool screens including the
  two Cytoscape graph screens.
- **Frontend image is nginx, not Python.** `make bundle` now ships a
  `chorus-frontend` nginx image tarball alongside `chorus-backend`. The
  ingestion CSV upload size limit is `CHORUS_CLIENT_MAX_BODY_SIZE` in the
  nginx config (default `512m`) rather than Streamlit's `server.maxUploadSize`
  — operators must update the vhost nginx config if they had tuned the old
  limit.
- **§76 audit attribution is now per-user correct.** The fixed service identity
  gap introduced by Streamlit's httpx proxy model is closed.
- **Frontend tests are Vitest** (not pytest). CI adds a dedicated frontend lint
  / typecheck / test step. The shared `nos-tromo/.github` python-app-ci workflow
  covers the backend; the frontend job is additive.
- **`chorus/ui/` is deleted** along with the `streamlit` dependency group in
  `pyproject.toml` and its tests under `tests/ui/`.

## Alternatives considered

- **Upgrade Streamlit and stay on Python.** Streamlit has improved (the
  `st.navigation` API enables conditional sidebar items; `st.connection`
  might help with httpx session reuse). The audit attribution issue would still
  require the Streamlit container to forward a real per-request identity from
  the browser — which Streamlit's server-side model makes structurally
  awkward. And family alignment would require a second upgrade cycle later.
- **Keep Streamlit; add React just for the graph screens.** Two UI frameworks
  in one repo; not worth the operational overhead.
- **Svelte or Vue.** No `@infra/ui` package for either; family alignment
  dictates React.

## Reversal

If the team needs to revert to a Python-backed UI (e.g. rapid tool prototyping
outweighs the audit requirement): restore the `chorus/ui/` Python package and
the `streamlit` dependency group, add a server-side identity forwarding mechanism
(e.g. Streamlit's `request_headers` attribute in combination with a trusted
header), and record the resulting audit attribution model in the compliance doc
before re-enabling. The React SPA can be kept alongside (different port) or
removed.
