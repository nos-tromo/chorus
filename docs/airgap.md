# airgap notes

Disabled telemetry knobs and non-obvious offline-readiness findings accumulate
here as dependencies are added. See *Airgapped operation* in
[`CLAUDE.md`](../CLAUDE.md) for the hard rules.

## React SPA (frontend)

**Build on internet-side CI, ship as a prebuilt nginx image.** The frontend is a
Vite/React SPA. `docker/Dockerfile.frontend` uses a two-stage build: a
`node:20-alpine` builder stage runs `pnpm install --frozen-lockfile` and
`pnpm build` to produce the static bundle, then the artifact is copied into an
`nginx:1.27-alpine` runtime image. The airgapped side loads the prebuilt image
and fetches nothing — no node, no npm/pnpm, no build tooling on the airgapped
host.

**Supply-chain pinning.** Both base images in `docker/Dockerfile.frontend` are
digest-pinned (e.g. `node:20-alpine@sha256:…` and `nginx:1.27-alpine@sha256:…`),
so the build is reproducible and immune to mutable-tag substitution attacks even
before the image reaches the internal registry.

**No runtime network or telemetry.** Three concrete guarantees:

- **Fonts** are bundled via the `@fontsource/inter` npm package — Inter is
  served as a same-origin static asset from the nginx image. No Google Fonts CDN
  call is ever made.
- **Graph rendering** uses Cytoscape.js, a pure-JS canvas library bundled into
  the static SPA. No WASM, no CDN requests, no subprocess.
- **CSP lockdown.** `frontend/nginx/security-headers.conf` emits a
  `Content-Security-Policy` header that restricts `connect-src`, `script-src`,
  `font-src`, and `img-src` to `'self'` (plus `data:` for img). External origins
  are structurally blocked at the HTTP level, not merely convention.

**Runtime configuration without build-time env baking.** The SPA learns the
active language (`RESPONSE_LANGUAGE`) and whether ingestion is enabled
(`INGESTION_UI_ENABLED`) by calling `GET /config` on the backend at startup. No
env vars are baked into the static bundle at build time; the same image is used
regardless of deployment language or ingestion flag.

## python-multipart

**Pure-Python form parser, no network, no telemetry** (`python-multipart`
0.0.29, added 2026-06-07 for ADR 0014). FastAPI requires it to parse
`multipart/form-data`, which the frontend ingestion endpoint
(`POST /ingestion/ingest`, `chorus/api/routers/ingestion.py`) uses to receive
uploaded CSV files. It is a parsing library only — it opens no sockets, calls no
home, ships no data files, and has no system dependencies, so it is offline-ready
the moment it is installed into the image's virtualenv by `uv sync --locked`.
The uploaded bytes are streamed to a staging directory under `CHORUS_HOME` and
fed to the existing file adapter; nothing leaves the host.
