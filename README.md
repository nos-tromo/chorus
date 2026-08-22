# chorus

GraphRAG system for social network analysis. See [`CLAUDE.md`](CLAUDE.md)
for the full architecture, data model, and scope; this README covers
just enough to get the app running locally. Everything else lives in
[`docs/`](docs/README.md).

## What it does

- Ingests social-media exports (postings, comments, messages, profiles,
  connections) into a people-centric Neo4j knowledge graph.
- Serves seven named graph retrieval tools over `POST /tools/<name>`,
  each with a Pydantic input/output schema and version-controlled Cypher.
- Answers free-text questions with an agent that selects and calls those
  tools — it never writes Cypher itself.
- Resolves the `:Alias` surface forms extraction writes onto canonical
  `:Entity` nodes, so results cluster by entity rather than by spelling.
- Logs every tool invocation to an append-only §76 BDSG audit log.

Inference is reached over the network at the shared vllm-service router;
chorus ships no model weights and runs airgapped in production. The
runtime surface is detailed in
[architecture.md](docs/architecture.md#runtime-surface).

## Prerequisites

- **Python 3.12.** `requires-python` in `pyproject.toml` pins the 3.12 line; `uv sync` picks (or downloads) a matching interpreter.
- **[uv](https://docs.astral.sh/uv/)** for dependency and venv
  management. `uv.lock` is the source of truth — don't hand-edit
  `requirements.txt`.
- **Docker** to run the app stack. The data-plane compose project
  (separate repo) owns Neo4j and must be up before starting chorus.
- **(Optional) An inference endpoint.** The graph tools don't
  exercise inference, so you can defer this. The agent (`/agent/query`),
  inline NER during ingestion, and the resolve stage do need it: point
  `OPENAI_API_BASE` at vllm-service's LiteLLM proxy (or any
  OpenAI-compatible endpoint), and `NER_API_BASE` at a GLiNER service.

## Quick start

### 1. Install dependencies

```bash
uv sync
```

This creates `.venv/` and installs both runtime and dev dependencies
from the lockfile.

### 2. Configure environment

Copy the example file and adjust the Neo4j and auth knobs for a
host-side process talking to a containerised Neo4j:

```bash
cp .env.example .env
```

Then edit `.env` so the URI points at the host-published bolt port
and a dev principal is allowed through:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=devpassword

CHORUS_DEFAULT_IDENTITY=dev
```

`CHORUS_DEFAULT_IDENTITY` is the dev-only fallback for the
trusted-header principal seam. Leave it unset in production — without
it, requests without an `X-Auth-User` header fail with 401.

### 3. Apply migrations

Migrations are idempotent and the app applies pending ones on
startup, but it's useful to run them explicitly the first time and
confirm the constraints and vector indexes land:

```bash
uv run python -m chorus.migrations.cli apply
uv run python -m chorus.migrations.cli status
```

### 4. Start the API

```bash
uv run uvicorn chorus.api.main:app --reload --port 8000
```

The lifespan opens the Neo4j driver, applies any remaining
migrations, and initialises the audit log SQLite file under
`./var/`.

### 5. (Optional) Start the frontend dev server

The React SPA can be developed locally with Vite's dev server, which proxies
API calls to the backend running in step 4. In a separate shell:

```bash
cd frontend
pnpm install          # first time only; uses the frozen lockfile
pnpm dev              # Vite dev server at http://localhost:5173
```

Vite proxies `/health`, `/config`, `/tools`, `/agent`, and `/ingestion` to
`http://localhost:8000`. Auth is handled by the `CHORUS_DEFAULT_IDENTITY=dev`
set in your `.env` — the dev server sends no identity header, and the backend
falls back to that value when `X-Auth-User` is absent.

To run the full compose stack (nginx-served SPA + backend):

```bash
make network    # create shared Docker networks (idempotent)
make volumes    # create the external chorus-state volume (idempotent)
make build      # build backend + frontend (nginx) images
make up-dev     # start backend (port 8000) + frontend (port ${CHORUS_FRONTEND_HOST_PORT:-8501})
```

The frontend is served by nginx (the unprivileged image, uid 101) on port
8080 inside the container; `make up-dev` publishes it on
`${CHORUS_FRONTEND_HOST_PORT:-8501}` on the host. Set
`INGESTION_UI_ENABLED=true` on the backend service to expose the ingestion
screen (the nav item and route are hidden by default).

## Smoke test

```bash
curl -s http://localhost:8000/health
# => {"status":"ok"}
```

If you get a 503 here, the API can't reach Neo4j — check `NEO4J_URI`
and that the container is up.

From there, [tutorial-first-queries.md](docs/tutorial-first-queries.md)
walks the surface end-to-end: the tool registry, seeding a row into the
empty graph, invoking a tool, and asking the agent the same question.

## Running the test suite

Unit tests stub the inference provider and run without external
services. Integration tests spin up an ephemeral Neo4j via
`testcontainers`, so Docker must be reachable from the shell running
pytest:

```bash
uv run pytest                 # everything
uv run pytest tests/inference # unit tests only — no Docker needed
uv run pytest tests/integration -k posts_mentioning   # a single case
```

The first integration-test run pulls `neo4j:5.26.26-community`, which
takes a minute. Subsequent runs reuse the image.

## Lint, format, type check

```bash
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pre-commit run --all-files
```

Pre-commit runs ruff and pyrefly on changed files; the full pytest
suite runs in CI, not in the hook.

## Bringing up the app via compose (optional)

The app's compose project lives in `docker/` and is wired up via the
top-level Makefile. It assumes Neo4j is already reachable on the
shared `data-net` Docker network as `neo4j:7687` — bring the
data-plane compose project up first (see *Orchestration topology*
in `CLAUDE.md`), then:

```bash
make help       # every target with a one-line description
```

The day-to-day path is `make network` / `volumes` / `build` / `up-dev`
(or `make dev`, which builds then brings the dev shape up), plus
`make migrate` / `ingest` / `resolve` for the data stages and `make down`
to stop (it never touches graph data). `make bootstrap` waits for
data-plane health first, `make bundle` / `bundle-dev` produce the airgap
image tarballs, and `make stop` / `logs` round out the lifecycle — see
`make help` for the full list rather than a copy of it here.

## Further reading

- [`CLAUDE.md`](CLAUDE.md) — architecture, data model, scope and
  anti-scope, airgap rules, compliance posture.
- [`docs/architecture.md`](docs/architecture.md) — the long-form
  architecture notes.
- [`docs/airgap.md`](docs/airgap.md) — what the airgapped production
  constraint implies for dependencies, images, and inference.
- [`docs/compliance.md`](docs/compliance.md) — §76 BDSG audit logging,
  retention, OIDC.
- [`docs/decisions/`](docs/decisions/) — ADRs for the load-bearing
  architectural choices.
