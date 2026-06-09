# 0014 — Frontend-triggered data ingestion (`INGESTION_UI_ENABLED`)

Status: accepted
Date: 2026-06-08

## Context

Ingestion was CLI/Makefile-only. `make ingest` runs
`python -m chorus.ingestion.cli run`, which reads CSVs from a **server-side**
directory (`INGESTION_SOURCE_DIR`, bind-mounted from `~/chorus/ingest` via
`docker/compose.override.yaml`). That shape is unusable for an analyst whose
table exports live on their **own machine**, and it forces non-CLI users out of
the product entirely — the Streamlit UI could query the graph but never build
it. The frontend experience was therefore incomplete end-to-end.

The pipeline these users need already exists and is well-tested: `apply_all`
(migrations), `run_once` (ingest, with inline NER), and `resolve_all`
(alias→entity). What was missing is a way to (a) get client-side files onto the
server and (b) drive those three stages from the UI, safely and observably,
without changing the pipeline.

Two facts shape the design. The UI is a separate container and a deliberately
thin HTTP client (business logic stays server-side), so the work must be new API
endpoints, not in-UI orchestration. And ingest-with-NER and resolve can each run
for **minutes** — far past the 30 s UI client timeout and typical reverse-proxy
limits — so a synchronous request is not viable at real data sizes.

## Decision

Add a UI path that uploads CSVs and runs migrate/ingest/resolve through the
**existing** pipeline, gated by a new flag. The CLI/bind-mount path is retained
unchanged for bulk/server-side loads.

1. **Flag, default off, backend-enforced.** `INGESTION_UI_ENABLED`
   (`utils/env_cfg.py::load_ingestion_ui_env`, read at call time) defaults to
   `false` — the data-mutating upload surface is never exposed by accident. Two
   routers in `api/routers/ingestion.py`: a gated `router`
   (`dependencies=[resolve_principal, require_ingestion_ui_enabled]` → 401 then
   403) for every action/data route, and an **ungated** `status_router` holding
   only `GET /ingestion/feature`, so the UI can distinguish "disabled" from
   "unreachable". The Streamlit page (`pages/07_data_ingestion.py`) queries
   `/feature` on load and self-renders a disabled notice when off.

2. **Upload → per-request staging → existing pipeline.** `POST /ingestion/ingest`
   takes `multipart/form-data` (`files`, optional `since`, `then_resolve`),
   streams each file to a fresh `tempfile.mkdtemp` **under `CHORUS_HOME/uploads`**
   (the `chorus-state` volume — works in the production container shape, no bind
   mount), and runs `FileUpstreamAdapter(staging) → run_once`. The worker removes
   its staging dir in a `finally`, so a crash or error never leaks files; the raw
   store already retains the verbatim rows, so the staged CSVs are redundant
   afterward. `python-multipart` is added for FastAPI form parsing (pure-Python,
   airgap-safe; see `docs/airgap.md`).

3. **Filename validation mirrors the adapter; the write re-derives a basename.**
   `FileUpstreamAdapter` only globs the five known table patterns and **silently
   ignores** anything else, so a mis-named upload would yield a green zero-row
   run. `upstream.table_for_filename` (sharing the `TABLES` constant) rejects any
   name that is not `<table>.csv` / `*_<table>.csv`, and the endpoint 422s on the
   first unrecognized name. Independently, the staging write reduces each name to
   `Path(name).name` and rejects any name that differs from its basename — so the
   write is provably inside the staging dir regardless of the validator (defense
   in depth; the two are kept in lockstep by intent, noted here).

4. **Background jobs + polling; ephemeral by design.** Long stages do not block
   the request. `ingestion/jobs.py::JobRegistry` (on `app.state.jobs`, created in
   the lifespan, shut down on teardown) runs work on a single-worker
   `ThreadPoolExecutor` and returns `202 {job_id}`; the UI polls
   `GET /ingestion/jobs/{id}`. Job state lives only in memory — a restart loses
   it, which is acceptable because the **results** are durable in Neo4j and the
   §76 audit log; the registry only carries progress for polling. A failed job
   is reported as `200` with the message in `error` (only an unknown id is `404`).

5. **One active job; migrate stays synchronous but gated.** `submit` rejects a
   second active job atomically with `JobBusyError` → `409`, so the single worker
   never overlaps two Neo4j writers and the UI gets a clean "busy" state. Migrate
   is fast and idempotent (and already runs at startup), so it stays synchronous —
   but it shares the same one-active-job `409` so its DDL cannot interleave an
   in-flight ingest. `then_resolve` chains resolution **inside the ingest job**
   (one polling target); a resolution failure preserves the ingest counts and is
   surfaced as `resolution_error` rather than discarding a successful ingest.

6. **Audit wiring.** `run_once` and `apply_all` do not self-audit, so the worker
   wraps ingest in `time_tool(user, "ingest", …)` and the route wraps migrate in
   `time_tool(user, "migrate", …)`. `resolve_all` **does** write its own
   `resolve_all` row (and none on an empty run), so neither `/resolve` nor the
   `then_resolve` chain wraps it — a successful chain therefore writes exactly two
   rows (`ingest`, `resolve_all`).

7. **Authorization = any authenticated user when enabled.** There is no RBAC in
   v1; the deploy-time flag is the capability gate and the §76 log records who ran
   each pass. Per-principal restriction is deferred (see Reversal).

## Consequences

- An analyst can run the whole pipeline from the browser in the production
  compose shape (no bind mount); `make ingest` remains for bulk/server-side loads.
- **Anti-scope reaffirmed.** This is still single-source ingestion of the same
  five tables — uploading does not make chorus multi-source (CLAUDE.md). The
  upload is a transport for the *defined* upstream format, nothing more.
- **Upload size is a three-hop concern** (browser → Streamlit `server.maxUploadSize`,
  200 MB default → backend reads the full body → staging). In production Nginx
  fronts the UI and its default `client_max_body_size` is **1 MB** → it 413s large
  uploads before they reach the app: operators must raise it on the chorus UI
  vhost. Steer bulk loads to the retained bind-mount path.
- **Partial success is still "done."** `run_once` drops malformed rows and filters
  orphan comments and still returns `done`; the page surfaces `dropped` /
  `filtered` / `skipped` so loss is visible. A run that raises mid-way leaves
  prior MERGE writes in place; re-running is safe because all writes are
  idempotent.
- **No durability across restart** for in-flight jobs or their progress; staging
  dirs are cleaned in the worker `finally`, and a `SIGKILL` mid-run can leave an
  orphan dir under `CHORUS_HOME/uploads` (a lifespan startup sweep is a cheap
  future add).
- Setting `INGESTION_UI_ENABLED=true` is required to expose any of this; the
  frontend learns the state from the backend (single source of truth), so only
  the `backend` service gets the var.

## Alternatives considered

- **Synchronous endpoints.** Rejected: ingest-with-NER and resolve routinely
  exceed the 30 s client timeout and proxy limits; the all-synchronous codebase
  would need raised timeouts everywhere and would still be fragile at scale.
- **A durable/persistent job queue (DB- or broker-backed).** Rejected for v1:
  the results are already durable; an in-memory registry is enough for a
  low-volume, single-worker UI feature and adds no infrastructure. Revisit if
  jobs must survive restarts or scale horizontally.
- **Hiding the page from the sidebar when disabled.** Rejected: Streamlit
  auto-discovers `pages/*.py`; hiding one requires migrating every page to the
  `st.navigation` API — out of scope. The page self-gates in its body instead.
- **Per-principal authorization / an allowlist env.** Deferred: there is no RBAC
  seam yet; the deploy flag plus the audit trail is the proportionate v1 control.
- **Orchestrating in the Streamlit process.** Rejected: violates the thin-client
  invariant and would require the UI container to hold Neo4j/inference config and
  a driver; business logic stays server-side.

## Reversal

- If uploads must survive restarts or run concurrently: replace the in-memory
  registry with a durable queue and, if raising worker count, add Neo4j
  write coordination (the one-active-job `409` is the current simplification).
- If least-privilege is required: add an `INGESTION_ALLOWED_USERS`-style check in
  the gate dependency (or a real RBAC seam) — the audit attribution is already in
  place.
