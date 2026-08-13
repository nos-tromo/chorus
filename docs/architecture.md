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

### Project selection seam (ADR 0017)

Identity and project travel separately. The gateway asserts which projects a
user may reach (`X-Auth-Projects`); the SPA picks one of them and sends it as
`X-Chorus-Project` on every request. Both are forwarded by the chorus nginx
alongside `X-Auth-User`. The selection is a *choice within* the claim, never a
grant: `resolve_project` re-derives the allow-list server-side and rejects
anything outside it, so a tampered header can only ever narrow access.

The SPA learns the list from `GET /whoami` at boot. That one endpoint resolves
the project leniently — `active_project` is null rather than a 400/403 when the
selection is absent, ambiguous, or stale — because it is the SPA's only source
for the list and so has to answer before a project has been chosen. Everything
that serves project data keeps failing closed.

`ProjectProvider` (`frontend/src/project/`) holds the selection, persists it in
`localStorage`, and blocks rendering until one exists, so no request can go out
unscoped. Switching projects evicts every project-scoped react-query entry
(identity and app config survive) and remounts the routes through
`<ProjectScoped>`, so accumulated explorer graphs, agent conversations, and job
polls cannot leak across the boundary. The sidebar shows a switcher only when
the claim covers more than one project; single-project deployments never see it.

### Ingestion upload limit

Nginx's `client_max_body_size` is env-templated (`CHORUS_CLIENT_MAX_BODY_SIZE`,
default `512m`) in `frontend/nginx/default.conf.template`. Social-graph
`connections.csv` exports can be large; operators must also raise the outer
reverse-proxy limit on the chorus vhost if they have a lower global default.

## Data-plane integration contract

chorus expects the data-plane Compose project to publish one Neo4j instance
per configured project on `data-net` (ADR 0017):

- `data-net` alias: `neo4j-<project>` (single-project compat mode: `neo4j`)
- bolt port: `7687`
- HTTP port: `7474`

With `CHORUS_PROJECTS` set, chorus reads `NEO4J_URI_<NAME>` (plus optional
`NEO4J_USER_<NAME>` / `NEO4J_PASSWORD_<NAME>` / `NEO4J_DATABASE_<NAME>`
overrides) per project; a configured project without its URI fails at
startup — there is no fallback to a shared instance. With `CHORUS_PROJECTS`
unset, the flat `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`,
`NEO4J_DATABASE` drive the single implicit `default` project. The chorus
repo does not declare any graph volumes; all graph state lives in the
data-plane project's per-project named volumes, and ending a project means
dropping its container and volume.

## Inference contract

All inference (chat, embed, rerank, NER) is reached through vllm-service's
LiteLLM proxy at `http://vllm-router:4000/v1`, OpenAI-protocol HTTP,
selected by the `model` field in each request.
