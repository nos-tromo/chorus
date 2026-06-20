# Final-review cleanup — report

Date: 2026-06-20

## Changes applied

### MUST-FIX

**1. `docs/airgap.md` — replaced stale Streamlit UI section**
Removed the "Streamlit UI" section (which documented removed `STREAMLIT_BROWSER_GATHER_USAGE_STATS`,
`STREAMLIT_SERVER_HEADLESS`, and `chorus/ui/network_dot.py` / dagre-d3 render path).
Replaced with a "React SPA (frontend)" section covering:
- Two-stage Vite/React build → prebuilt nginx image; airgapped side loads and fetches nothing
- Digest-pinned base images (`node:20-alpine@sha256:…`, `nginx:1.27-alpine@sha256:…`)
- `@fontsource/inter` bundles fonts (no Google Fonts CDN)
- Cytoscape.js pure-JS canvas (no WASM/CDN)
- CSP in `frontend/nginx/security-headers.conf` locks `connect-src`/`script-src`/`font-src`/`img-src` to `'self'`
- Runtime config via `GET /config` (no build-time env baking)

**2. Dead code deleted**

- Removed 5 unused i18n keys from both `frontend/src/i18n/en.ts` and `frontend/src/i18n/de.ts`
  (confirmed zero non-catalog references before deletion):
  - `agent.trace_error`
  - `posts.hits`
  - `topic_cooc.find`
  - `topic_cooc.count`
  - `authors_mentioning.count`

- Deleted `frontend/src/components/form/useToolForm.ts` (confirmed no importers outside the test file).
  Removed its `describe('useToolForm', ...)` block and its `renderHook`/`act` import from
  `frontend/src/components/form/form.test.tsx`.

- Removed `placeholderKey?` from `EntityFieldSpec` and `TextFieldSpec` in `frontend/src/tools/specs.ts`
  (ToolScreen.tsx confirmed never reads it). Removed `payloadKey?` from `EntityFieldSpec`
  (ToolScreen hardcodes `'_entity'` for entity kind; `payloadKey` on `EntityFieldSpec` was dead).

### POLISH

**3. `frontend/src/components/GraphCanvas.tsx` — i18n the "Fit" button**
Added `'graph.fit': 'Fit'` (en) and `'graph.fit': 'Einpassen'` (de) to both catalogs.
GraphCanvas now imports `useT()` from `../config/ConfigContext` and renders `{t('graph.fit')}`.
Updated `GraphCanvas.test.tsx` to wrap renders in `ConfigProvider` (matching the ToolTrace test
pattern) and made all tests async with `waitFor`/`findBy*` to handle the async config load.

**4. `docker/compose.yaml` — stale comment**
Updated comment on `INGESTION_UI_ENABLED` from "the frontend asks /ingestion/feature"
to "the SPA reads GET /config at startup".

## Test results

### Frontend (`pnpm typecheck && pnpm lint && pnpm test`)

```
pnpm typecheck → exit 0 (no errors)
pnpm lint      → exit 0 (no errors)
pnpm test      → 21 test files, 157 tests, 157 passed, 0 failed
```

(The `socket hang up / ECONNREFUSED :3000` in test output is a pre-existing artefact
from a network-call mock that didn't suppress the initial connection attempt — all tests pass.)

### Backend (`uv run pytest -q`)

```
308 passed, 5 warnings in 48.70s
```

(The 5 warnings are pre-existing FastAPI deprecation notices unrelated to this change set.)

## Verification checklist

- [x] `grep -rin "streamlit" docs/airgap.md` → empty
- [x] Dead i18n keys: `grep -rn "agent.trace_error\b|posts.hits|topic_cooc.find|topic_cooc.count|authors_mentioning.count" frontend/src --include=*.ts --include=*.tsx | grep -v "i18n/"` → empty
- [x] `grep -rn "useToolForm" frontend/src` → no importers outside deleted file
- [x] `grep -n "payloadKey|placeholderKey" frontend/src/components/ToolScreen.tsx` → no results for `placeholderKey`; `payloadKey` references are all on `TextFieldSpec` (which still has `payloadKey: string`)
- [x] i18n parity test (`en and de have identical key sets`) passes
- [x] form.test.tsx EntityInput/LimitField/SubmitButton tests still pass
