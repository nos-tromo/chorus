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

For the nginx-served SPA instead of the Vite dev server, see *Operating*.

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

## Operating

The Makefile wraps both halves of the workflow; `make help` lists every
target with a one-line description.

```bash
make verify     # pre-push gate: pre-commit (ruff + pyrefly) + frontend lint/build
make test       # pytest + vitest (test-backend / test-frontend for one half)
make dev        # build backend + frontend images, then bring the dev shape up
```

Integration tests spin up an ephemeral `neo4j:5.26.26-community` via
`testcontainers`, so Docker must be reachable from the shell running
pytest; the first run pulls the image. Unit tests stub the inference
provider and need no services (`uv run pytest tests/inference`).
Pre-commit runs ruff and pyrefly on changed files; the full pytest suite
runs in CI, not in the hook.

The compose project lives in `docker/` and assumes Neo4j is already
reachable on the shared `data-net` Docker network as `neo4j:7687` — bring
the data-plane compose project up first (see *Orchestration topology* in
[`CLAUDE.md`](CLAUDE.md)), or let `make bootstrap` wait for its health.
The frontend is nginx (the unprivileged image, uid 101) on port 8080
inside the container, published on `${CHORUS_FRONTEND_HOST_PORT:-8501}` by
`make up-dev`; `make down` never touches graph data. Set
`INGESTION_UI_ENABLED=true` on the backend service to expose the ingestion
screen (the nav item and route are hidden by default).

## Further reading

- [`docs/README.md`](docs/README.md) — index of the in-repo documentation.
- [`CLAUDE.md`](CLAUDE.md) — architecture, data model, scope and
  anti-scope, airgap rules, compliance posture.
- [`docs/tutorial-first-queries.md`](docs/tutorial-first-queries.md) — the
  guided first pass over the tool and agent surface.
- [`docs/ingestion.md`](docs/ingestion.md) — running the ingestion and
  alias-resolution stages.
- [`docs/architecture.md`](docs/architecture.md) — the long-form
  architecture notes.
- [`docs/airgap.md`](docs/airgap.md) — what the airgapped production
  constraint implies for dependencies, images, and inference.
- [`docs/compliance.md`](docs/compliance.md) — §76 BDSG audit logging,
  retention, OIDC.
- [`docs/retention.md`](docs/retention.md) — retention timers and the
  cascade rules for expiry.
- [`docs/decisions/`](docs/decisions/) — ADRs for the load-bearing
  architectural choices.

Questions, bugs and feature requests:
<https://github.com/nos-tromo/chorus/issues>.
