# chorus

GraphRAG system for social network analysis. See [`CLAUDE.md`](CLAUDE.md)
for the full architecture, data model, and scope; this README covers
just enough to get the app running locally and to fire the first few
queries against it.

## What works today

The foundation is up: the FastAPI app boots, applies Neo4j migrations
on startup, exposes `/health`, and dispatches one reference tool
(`posts_mentioning`) end-to-end with audit logging. Ingestion beyond
the adapter skeleton, the data-plane compose project, and the
retention sweeper are still to land — see *Current state* in
`CLAUDE.md` for the punch list.

## Prerequisites

- **Python 3.11–3.13.** Development pins `3.12` via `.python-version`.
- **[uv](https://docs.astral.sh/uv/)** for dependency and venv
  management. `uv.lock` is the source of truth — don't hand-edit
  `requirements.txt`.
- **Docker** to run the app stack. The data-plane compose project
  (separate repo) owns Neo4j and must be up before starting chorus.
- **(Optional) An inference endpoint.** The reference tool doesn't
  exercise inference, so you can defer this. When you do need it,
  point `OPENAI_API_BASE` at vllm-service's LiteLLM proxy (or any
  OpenAI-compatible endpoint).

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

### 5. (Optional) Start the Streamlit UI

In a separate shell:

```bash
CHORUS_API_URL=http://localhost:8000 \
  uv run streamlit run chorus/ui/streamlit_app.py
```

## First test iterations

With the API running, exercise the surface end-to-end.

### Liveness

```bash
curl -s http://localhost:8000/health
# => {"status":"ok"}
```

If you get a 503 here, the API can't reach Neo4j — check `NEO4J_URI`
and that the container is up.

### Tool registry

```bash
curl -s http://localhost:8000/tools | jq
```

Lists every registered tool with its Pydantic input/output schemas.
Today that's `posts_mentioning`.

### Seed a posting and an entity

The graph is empty after migrations, so the tool will return zero
hits. Seed one row directly via the Neo4j browser
(<http://localhost:7474>) or `cypher-shell`:

```cypher
MERGE (e:Entity {id: 'ent-berlin'})
  ON CREATE SET e.canonical_name = 'Berlin';
MERGE (p:Post:Posting {uuid: 'p-1'})
  ON CREATE SET p.text = 'hello berlin',
                p.timestamp = datetime();
MERGE (p)-[:MENTIONS]->(e);
```

### Invoke `posts_mentioning`

```bash
curl -s -X POST http://localhost:8000/tools/posts_mentioning \
  -H 'Content-Type: application/json' \
  -H 'X-Auth-User: dev' \
  -d '{"entity": "Berlin", "limit": 10}' | jq
```

You should see one hit referencing `p-1` and `ent-berlin`. The same
call writes one row to the audit log:

```bash
sqlite3 var/audit.sqlite \
  "SELECT user, tool_name, result_count, status FROM audit_log;"
```

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
uv run mypy .
uv run pre-commit run --all-files
```

Pre-commit runs ruff and mypy on changed files; the full pytest
suite runs in CI, not in the hook.

## Bringing up the app via compose (optional)

The app's compose project lives in `docker/` and is wired up via the
top-level Makefile. It assumes Neo4j is already reachable on the
shared `inference-net` Docker network as `neo4j-chorus:7687` — bring
the data-plane compose project up first (see *Orchestration topology*
in `CLAUDE.md`), then:

```bash
make network    # create the shared inference-net (idempotent)
make build      # build api + ui images
make up         # start api + ui (production shape, no host ports)
make up-dev     # like 'up', but publishes backend + frontend ports on the host
make migrate    # apply Neo4j migrations from inside the api container
make down       # stop + remove containers (never touches graph data)
```

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
