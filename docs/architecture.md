# chorus architecture

See [`CLAUDE.md`](../CLAUDE.md) for the canonical design. This file accrues
operational details (deployment topology, data-plane contract, runtime
diagrams) as they stabilize.

## Runtime surface

The FastAPI app boots, applies Neo4j migrations on startup, and exposes
`/health`. On top of that:

- **Nine graph tools** dispatched end-to-end, each call recorded on the
  §76 BDSG audit trail ([audit.md](audit.md)) — seven retrieval tools
  (`posts_mentioning`, `authors_mentioning`, `author_activity_summary`,
  `topic_co_occurrence`, `authors_connected_by_topic`, `network_around`,
  `social_network_around`) plus two click-to-expand tools
  (`expand_network_node`, `expand_social_node`; ADR 0016). Each has a
  Pydantic input/output schema and version-controlled Cypher under
  `chorus/queries/`. The whole registry is served at `/tools` and
  advertised to the agent — both iterate the same `TOOLS` dict. The two
  `*_around` and two `expand_*` tools return nodes-and-edges payloads
  the UI renders as network graphs.
- **A natural-language agent** at `POST /agent/query` (ADR 0009). It
  selects and calls the registered tools via OpenAI tool-calling to
  answer a free-text question — it never writes Cypher itself.
- **A React SPA** (Vite + TypeScript + Tailwind v4, `@infra/ui`) served by
  nginx, with one screen per tool, an agent screen, and a data-ingestion
  screen — upload CSV exports and run migrate/ingest/resolve as background
  jobs, gated by `INGESTION_UI_ENABLED` (default off; ADR 0014). The two
  `*_around` tools render interactive `ForceGraph` network graphs
  (`@infra/ui`, ADR 0016), with click-to-expand neighborhoods and inline
  graphs in agent answers.
- **Migrations** (constraints, indexes, vector indexes) applied in order
  and idempotently, with a CLI (`apply` / `status`).

*Current state* in [`CLAUDE.md`](../CLAUDE.md) tracks what has not landed
yet; the ingestion and resolution stages are in
[ingestion.md](ingestion.md).

## Frontend tier (React SPA + nginx)

The chorus frontend is a React Single-Page Application built with Vite, served
by an nginx container (ADR 0015). Nginx reverse-proxies eight API route
prefixes to the backend on port 8000 — `/ingestion` in its own location block
(it carries the CSV uploads, so it gets the raised body limit and unbuffered
proxying), and `/health`, `/config`, `/tools`, `/agent`, `/stats`, `/version`,
`/whoami` in the general JSON block. That makes the whole surface same-origin
from the browser's perspective — no CORS middleware is needed.

A new backend route has to be added in **two** places or it silently 404s
through the SPA fallback: the prefix regex in
`frontend/nginx/default.conf.template` and `API_PREFIXES` in
`frontend/vite.config.ts`. `/whoami` was missed once already.

### SPA bootstrap and language

The SPA fetches `GET /config` (unauthenticated, like `/health`) at startup to
get `{language, ingestion_enabled}`. `RESPONSE_LANGUAGE` and
`INGESTION_UI_ENABLED` live on the backend only; the SPA reads them via this
endpoint. No runtime toggle is exposed in the UI — the language is fixed at
boot from the backend env.

chorus defaults to English. `RESPONSE_LANGUAGE=de` switches the whole app to
German: the agent answers in German, strips leading articles when building
entity queries, and the SPA renders its captions in German. Unknown values
fall back to English. See ADR 0013 and ADR 0015.

### Authentication seam

The SPA's API client sends **no** identity header. In production, browser
requests pass through the `edge-plane` gateway — Caddy, with Authelia as
forward-auth — which strips any client-supplied identity headers and injects
the trusted `X-Auth-User`; the chorus nginx forwards that header unchanged to
the backend. The backend's `api/auth/principal.py` seam reads it and falls
back to `CHORUS_DEFAULT_IDENTITY` when absent (dev only; production leaves it
unset, so an unheadered request is rejected). This is what lets the audit
trail attribute every event to the real per-user principal
([audit.md](audit.md)).

#### Edge secret

The backend is also reachable on the shared `inference-net` and `data-net`
networks (alias `chorus-backend`), where any container could send its own
`X-Auth-User`. `CHORUS_EDGE_SECRET` closes that path. The gateway sends a
per-app shared secret in `X-Edge-Secret` on every request it proxies to
chorus and strips any copy a client sent; the chorus nginx passes it through
with the other request headers. With the secret set:

- The identity headers — `X-Auth-User` (or whatever `CHORUS_AUTH_HEADER`
  names) and the decorative `X-Auth-Name` shown by `/whoami` — count only on
  a request whose `X-Edge-Secret` equals the configured value, compared in
  constant time.
- On any other request they are ignored as if absent, and the request gets
  `401`: with the secret set, `CHORUS_DEFAULT_IDENTITY` never applies (the
  backend warns once at startup when both are set). If it sent an
  identity header, the backend logs a warning with the method and path only —
  no header value, no query string, never the secret.
- The secret must be at least 32 characters, or the backend refuses to start.
  Generate it with `make secret` in edge-plane; the same value goes into
  edge-plane's `.env` and chorus's.

Unset or blank, the identity headers are trusted from any caller, as before
the secret existed; when `CHORUS_DEFAULT_IDENTITY` is unset too, the backend
logs one warning at startup saying so.

Rollout order: the gateway must send the secret before chorus enforces it.
Apply the new `.env` to edge-plane first (`make up` there recreates the
gateway), then to chorus. In the other order, requests lose their identity and
get `401` until the gateway catches up. The gateway's side of the contract is
edge-plane's `docs/identity-contract.md`.

### Ingestion upload limit

Nginx's `client_max_body_size` is env-templated (`CHORUS_CLIENT_MAX_BODY_SIZE`,
default `512m`) in `frontend/nginx/default.conf.template`. Social-graph
`connections.csv` exports can be large; operators must also raise the outer
reverse-proxy limit on the chorus vhost if they have a lower global default.

## Data-plane integration contract

chorus expects the data-plane Compose project to publish a Neo4j service on
`data-net`:

- network alias: `neo4j` (hence the `NEO4J_URI=bolt://neo4j:7687` default)
- bolt port: `7687`
- HTTP port: `7474`

chorus reads `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
from its env. **All graph state** lives in the data-plane project's named
volumes — chorus declares none of it. chorus does declare one volume of its
own, `chorus-state` (the raw store, upload staging and the frozen legacy audit
file under `CHORUS_HOME`), and it is `external: true`, so
`docker compose down -v` in this repo cannot destroy it either.

## Inference contract

Chat, embed and rerank are reached through vllm-service's LiteLLM proxy at
`http://vllm-router:4000/v1`, OpenAI-protocol HTTP, selected by the `model`
field in each request. `chorus/inference/provider.py` is the only module that
knows any of that.

NER is the exception. It does **not** go through `provider.py` and is not a
model-field-routed task: `chorus/inference/ner_client.py` POSTs the
GLiNER-native `{text, labels, threshold}` body to `{NER_API_BASE}/gliner`,
with its own env family (`NER_API_BASE`, default `http://vllm-router:4000` —
note no `/v1` — plus `NER_API_KEY`, `NER_THRESHOLD`, `NER_TIMEOUT`,
`NER_ENABLED`). Keeping it decoupled from `INFERENCE_PROVIDER` is what lets a
host run one provider for chat/embed/rerank and vllm-service's ner-only stack
(`NER_API_BASE=http://gliner-ner:8000`) for NER. See *Inference provider
abstraction* in [`CLAUDE.md`](../CLAUDE.md).

Every inference call — chat, embed, rerank and NER — carries the request's
`X-Request-ID` (background jobs carry the id of the request that started
them), so a model call can be matched to its audit events; nothing else about
the caller is sent (`chorus/inference/correlation.py`).

## Observability

```bash
curl -s http://localhost:8000/metrics | head
```

Unauthenticated, like `/health`, so the obs-plane Prometheus scraper can
reach it without a principal header. Reports aggregate request counters
and latency histograms only — no user data. Set `METRICS_ENABLED=false`
to disable.

### Audit events

The backend's stdout carries the data-protection audit stream and nothing
else: operational logs, including uvicorn's access log, go to stderr. With
`AUDIT_OUTPUT=container` (set in `docker/compose.yaml`), CLI runs started
with `docker compose exec` write to the same stream. What is recorded is in
[audit.md](audit.md); obs-plane collects, stores and verifies it.

