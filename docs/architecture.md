# chorus architecture

See [`CLAUDE.md`](../CLAUDE.md) for the canonical design. This file accrues
operational details (deployment topology, data-plane contract, runtime
diagrams) as they stabilize.

## Frontend tier (React SPA + nginx)

The chorus frontend is a React Single-Page Application built with Vite, served
by an nginx container (ADR 0015). Nginx reverse-proxies the API route prefixes
(`/health`, `/config`, `/tools`, `/agent`, `/ingestion`) to the backend on port
8000, making the whole surface same-origin from the browser's perspective — no
CORS middleware is needed.

### SPA bootstrap and language

The SPA fetches `GET /config` (unauthenticated, like `/health`) at startup to
get `{language, ingestion_enabled}`. `RESPONSE_LANGUAGE` and
`INGESTION_UI_ENABLED` live on the backend only; the SPA reads them via this
endpoint. No runtime toggle is exposed in the UI — the language is fixed at
boot from the backend env.

### Authentication seam

The SPA's API client sends **no** identity header. Browser requests pass
through the upstream Nginx/OIDC proxy, which injects `X-Auth-User`; the chorus
nginx forwards that header unchanged to the backend. The backend's
`api/auth/principal.py` seam reads it and falls back to
`CHORUS_DEFAULT_IDENTITY` when absent (dev only). This ensures the §76 BDSG
audit log records the real per-user OIDC principal on every tool invocation.

### Ingestion upload limit

Nginx's `client_max_body_size` is env-templated (`CHORUS_CLIENT_MAX_BODY_SIZE`,
default `512m`) in `frontend/nginx/default.conf.template`. Social-graph
`connections.csv` exports can be large; operators must also raise the outer
reverse-proxy limit on the chorus vhost if they have a lower global default.

## Data-plane integration contract

chorus expects the data-plane Compose project to publish a Neo4j service on
`data-net`:

- service name: `neo4j-chorus`
- bolt port: `7687`
- HTTP port: `7474`

chorus reads `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
from its env. The chorus repo does not declare any persistent volumes; all
graph state lives in the data-plane project's named volumes.

## Inference contract

All inference (chat, embed, rerank, NER) is reached through vllm-service's
LiteLLM proxy at `http://vllm-router:4000/v1`, OpenAI-protocol HTTP,
selected by the `model` field in each request.
