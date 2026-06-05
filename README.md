# chorus

GraphRAG system for social network analysis. See [`CLAUDE.md`](CLAUDE.md)
for the full architecture, data model, and scope; this README covers
just enough to get the app running locally and to fire the first few
queries against it.

## What works today

The FastAPI app boots, applies Neo4j migrations on startup, and exposes
`/health`. On top of that, the following is working and stable on `main`:

- **Four graph retrieval tools** dispatched end-to-end with §76 BDSG
  audit logging: `posts_mentioning`, `author_activity_summary`,
  `topic_co_occurrence`, and `authors_connected_by_topic`. Each has a
  Pydantic input/output schema and version-controlled Cypher under
  `chorus/queries/`. The registry is served at `/tools`.
- **A natural-language agent** at `POST /agent/query` (ADR 0009). It
  selects and calls the registered tools via OpenAI tool-calling to
  answer a free-text question — it never writes Cypher itself.
- **An ingestion pipeline CLI** (`python -m chorus.ingestion.cli run`)
  that pulls the upstream tables (postings, comments, messages,
  profiles, connections), persists rows to the SQLite raw store, and
  projects them into the graph. Entity extraction (GLiNER NER) runs
  inline per post and writes `:MENTIONS` edges to `:Alias` nodes with
  provenance when `NER_ENABLED` is set.
- **An entity-resolution stage** (`python -m chorus.ingestion.cli resolve`)
  that clusters the `:Alias` nodes extraction writes onto canonical
  `:Entity` nodes — vector similarity + a same-type filter + an LLM
  tie-break, minting a new entity when nothing matches — and records
  `:RESOLVED_TO` provenance. It is idempotent, and because the four tools
  read through `:RESOLVED_TO`, a resolve pass upgrades them with no tool
  change ("Berlin" and "berlin" collapse to one entity).
- **A Streamlit UI** with one page per tool plus an agent page.
- **Migrations** (constraints, indexes, vector indexes) applied in order
  and idempotently, with a CLI (`apply` / `status`).
- **App compose project + Makefile** for building and running the api
  and ui services.

Still on the roadmap — the `semantic_search` / `network_around` /
`escape_hatch_cypher` tools, the retention sweeper job, and real OIDC
wiring — is tracked in `docs/` and `docs/decisions/`. See *Current state*
in `CLAUDE.md` for the punch list.

## Prerequisites

- **Python 3.11–3.13.** Development pins `3.12` via `.python-version`.
- **[uv](https://docs.astral.sh/uv/)** for dependency and venv
  management. `uv.lock` is the source of truth — don't hand-edit
  `requirements.txt`.
- **Docker** to run the app stack. The data-plane compose project
  (separate repo) owns Neo4j and must be up before starting chorus.
- **(Optional) An inference endpoint.** The four graph tools don't
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

chorus defaults to English. Set `RESPONSE_LANGUAGE=de` in `.env` to switch
the whole app to German: the agent answers in German and strips leading
articles when building entity queries (`die AfD` → `AfD`), and the Streamlit
UI renders its captions in German. This is the same variable docint reads, so
one setting flips both apps; it must live in this repo-root `.env` because
`docker compose` interpolates it into both the backend and frontend services.
Unknown values fall back to English. See ADR 0013.

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

Lists every registered tool with its Pydantic input/output schemas:
`posts_mentioning`, `author_activity_summary`, `topic_co_occurrence`,
and `authors_connected_by_topic`.

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

The other three tools (`author_activity_summary`, `topic_co_occurrence`,
`authors_connected_by_topic`) are invoked the same way — `POST
/tools/<name>` with the body matching the schema from `/tools`.

### Ask the agent

The agent answers free-text questions by selecting and calling those
tools. The server is stateless, so the client sends the visible
conversation on each request. This path needs a reachable, OpenAI-compatible
inference endpoint that supports tool-calling (`OPENAI_API_BASE` /
`TEXT_MODEL`):

```bash
curl -s -X POST http://localhost:8000/agent/query \
  -H 'Content-Type: application/json' \
  -H 'X-Auth-User: dev' \
  -d '{"messages": [{"role": "user", "content": "Which posts mention Berlin?"}]}' | jq
```

The response carries the agent's `answer`, a `trace` of the tool calls
it made, and a `truncated` flag. The turn is logged as a parent
`agent_query` audit row; each tool it calls writes its own row.

## Ingesting data

The ingestion pipeline reads CSV dumps of the upstream tables from
`INGESTION_SOURCE_DIR`, writes them to the SQLite raw store, and projects
them into the graph:

```bash
uv run python -m chorus.ingestion.cli run             # one full pass
uv run python -m chorus.ingestion.cli run --since 2026-01-01T00:00:00
```

`--since` restricts the pull to rows newer than the cutoff. Entity
extraction runs inline per post when `NER_ENABLED=true` and a GLiNER
endpoint is configured (`NER_API_BASE`); leave it off in dev
environments without one to avoid a connect-failure warning per post.

Extraction attaches each span to an `:Alias` node. Once a pull (with NER)
has run, resolve those aliases onto canonical entities:

```bash
uv run python -m chorus.ingestion.cli resolve
```

This clusters the unresolved `:Alias` nodes onto `:Entity` nodes — vector
similarity over `Entity.embedding` plus a same-type filter and an LLM
tie-break, minting a new entity when nothing matches — and writes
`:RESOLVED_TO` provenance. It needs the inference endpoint (it embeds the
surface forms and asks the chat model to break ties) and is idempotent, so
a re-run only resolves aliases added since. Because the four tools read
through `:RESOLVED_TO`, a resolve pass clusters their results by canonical
entity with no tool change. Thresholds are env-driven
(`RES_EMBED_THRESHOLD`, `RES_LLM_TIEBREAK`, `RES_VECTOR_K`).

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
make ingest     # run one ingestion pass from INGESTION_SOURCE_DIR
make resolve    # resolve aliases to canonical entities
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
