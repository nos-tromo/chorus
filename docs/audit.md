# Data-protection audit log

Chorus records each processing of personal data that §76 BDSG distinguishes —
collection, alteration, query, disclosure including transfer, combination and
erasure — as one event on the federation's audit stream: who did it, when, for
what stated purpose, what it touched, and where data went. Events are
hash-chained JSON lines written by the shared library
(`chorus/utils/audit_event.py`, vendored verbatim, never edited here).

Chorus only writes events. Storing, retaining, verifying and reading them is
obs-plane's job: see obs-plane's
[`docs/audit.md`](https://github.com/nos-tromo/obs-plane/blob/main/docs/audit.md)
for the store, its retention, who may read it, and the chain verifier.

## How events leave chorus

- **One chain per process.** The backend opens its chain in the FastAPI
  lifespan, before it touches the graph, and closes it last at shutdown. Every
  audited CLI command opens a chain of its own and closes it when it exits.
  Each chain starts with a `lifecycle` / `start` event and one `admin` /
  `config.audit` event stating the operating mode, beats every 60 seconds
  (`lifecycle` / `heartbeat`) and ends with `lifecycle` / `shutdown`.
- **`config.audit` criteria:** `edge_secret_enforced` (whether
  `CHORUS_EDGE_SECRET` is set), `default_identity_enabled` (whether the dev
  fallback identity is in effect: set, and no edge secret) and
  `require_purpose` (the sorted actions that need a stated purpose). CLI
  chains state `require_purpose` only. No secrets, no paths.
- **Where the lines go.** `docker/compose.yaml` sets `AUDIT_OUTPUT=container`:
  every process in the backend container writes to the container's main
  stdout under one container-wide lock, so a command started with
  `docker compose exec` (the `make migrate` / `ingest` / `resolve` targets)
  lands in the backend's log next to the server's chain, and lines from
  concurrent writers never interleave. Do not run audited commands in a
  one-off `docker compose run --rm` container: it can exit before the log
  collector sees it, and its events would be lost.
- **stdout carries nothing else.** Operational logs go to stderr (loguru;
  uvicorn's access log is moved there at startup). The log collector routes
  every stdout line containing `"stream":"audit"` to the audit store.

## Who, why, and which request

| Situation | Actor |
|---|---|
| Identity header trusted by the principal seam (`X-Auth-User`) | `{"user": <account>, "via": "edge"}` |
| Dev fallback `CHORUS_DEFAULT_IDENTITY` (only without `CHORUS_EDGE_SECRET`) | `{"user": <default identity>, "via": "system"}` |
| CLI command | `{"user": AUDIT_CLI_USER, "via": "cli"}` (`null` when unset) |
| Startup migrations, the chain's own events | `{"user": null, "via": "system"}` |

- **Unauthenticated requests (401) are not audited**: they have no actor, and
  recording them would let anyone on the shared networks flood the trail. A
  forged identity (wrong or missing `X-Edge-Secret`) is unauthenticated.
- **Stated purpose.** A request states its purpose in `X-Audit-Purpose`, the
  form-encoded serialization of up to two fields, `case_ref` and `reason`
  (what `new URLSearchParams({case_ref, reason}).toString()` produces). It is
  recorded on the request's events, and nowhere else — never in the
  operational log. A malformed, oversized or ambiguous value is a `400` with
  a generic body and writes nothing. A CLI run states it in `AUDIT_CASE_REF`
  / `AUDIT_REASON`; a malformed value exits 2 before the chain starts.
- **Required purpose.** `AUDIT_REQUIRE_PURPOSE` lists the actions that need a
  stated purpose. `docker/compose.yaml` sets it to `query,disclose`, so a
  deployment refuses every query and disclosure without one (§76 BDSG
  Abs. 2 asks the reason for both): tool calls, `/stats` and agent turns.
  Collection, combination, modification and administration — the ingestion
  routes and the make targets — still run without one. An operation whose
  actions include a required one, without a stated purpose, is refused
  before it processes anything: one event with `outcome="denied"` (same
  action and operation, its criteria, no target) and a `428` with a generic
  body — exit code 2 for a CLI command. `AUDIT_REQUIRE_PURPOSE=` (empty) in
  `.env` turns the requirement off; unset keeps the compose default. The
  code default is empty, so a bare `uvicorn` run and the test suite require
  nothing unless the variable is set. An unknown action name stops startup.
- **Refusals.** An authenticated request refused for a missing purpose
  (`428`), or refused at the ingestion feature gate (`403`) on one of the
  three ingestion action routes, is recorded as `denied`.
- **Correlation.** The gateway's `X-Request-ID`, when it is a sane id, is
  copied onto every event of the request and forwarded as a header on the
  inference calls the request makes (never the identity, never the purpose).
- **Background jobs** (UI ingest and resolve) write their events with the
  actor, purpose and request id of the request that started them.

## Stating the purpose in the SPA

The SPA's header carries a purpose bar (`PurposeBar` from `@infra/ui`). The
user states a case reference, a short reason, or both; the purpose holds for
the browser tab (`sessionStorage`, scope `chorus`) until it is changed or
cleared, and signing out clears it. `GET /config` reports which actions the
backend refuses without a purpose (`require_purpose`, sorted); while that
list is non-empty, an empty bar shows *Purpose required*.

Every API request goes out through one fetch wrapper
(`frontend/src/api/client.ts`), which adds the stated purpose as
`X-Audit-Purpose`. A `428` opens the bar's dialog: once the user saves, the
request is sent once more with the purpose; a cancelled dialog leaves the
refusal standing, and the screen says that a purpose is needed. A query
refused that way (the dashboard's `/stats`) loads again by itself once a
purpose is stated; a refused form is submitted again by the user.

| SPA request | Actions | Purpose needed by default | How it carries the purpose |
|---|---|---|---|
| `POST /tools/{name}` (tool screens, explorer seed and expand) | `query` | yes | `X-Audit-Purpose`, from the wrapper |
| `GET /stats` (dashboard) | `query` | yes | wrapper |
| `POST /agent/query` (chat) | `query`, `ai_call` | yes | wrapper; the turn's tool calls run in the same request and record its purpose |
| `POST /ingestion/ingest` (CSV upload, multipart) | `collect`, `delete`, `ai_call` with NER, the resolution actions with *then resolve* | no | wrapper; on a `428` the same form data is sent again |
| `POST /ingestion/resolve` | `combine`, `ai_call`, `modify` | no | wrapper |
| `POST /ingestion/migrate` | `admin`, plus `modify` for a data-changing migration | no | wrapper |
| `GET /config`, `/whoami`, `/health`, `/tools`, `/ingestion/migrations`, `/ingestion/jobs/{id}` | not recorded | no | wrapper (sent and ignored) |
| Explorer exports (JSON, GraphML, HTML) | none: no request | no | built in the browser from an audited tool result |

*By default* means the compose default `query,disclose`. An audited request
that carries a purpose records it, whether it was needed or not.

Nothing else reaches the backend: the SPA has no `<a href>` download,
`<img>` or media source, `EventSource`, `window.open` or native form post
pointing at an API route, so no request can bypass the wrapper.

## What is recorded: HTTP endpoints

| Endpoint | Recorded as | Notes |
|---|---|---|
| `POST /tools/{name}` | `query` / `tool.<name>` | One event per call, before the result is returned. An unknown tool (404) or invalid input (422) runs no operation and writes none. |
| `GET /stats` | `query` / `stats.read` | The top authors and top entities are personal data. |
| `POST /agent/query` | `query` / `tool.<name>` per tool call, then `ai_call` / `agent.answer` | See *Agent turns* below. |
| `POST /ingestion/ingest` | `collect` / `ingest.upload`, `ai_call` / `ingest.ner`, `delete` / `ingest.staging.delete` | Written by the job; with `then_resolve` the resolution events follow the collection. |
| `POST /ingestion/resolve` | `combine` / `resolution.run`, `ai_call` / `resolution.embed` and `resolution.tiebreak`, `modify` / `resolution.relink` | Written by the job; an empty run writes nothing. |
| `POST /ingestion/migrate` | `admin` or `modify` / `migration.apply` per applied migration | Nothing pending: nothing written. A refusal at the feature gate is `admin` / `migration.apply`, `denied`. |
| `GET /health`, `GET /config`, `GET /version` | not recorded | Liveness and bootstrap configuration; no personal data. |
| `GET /whoami` | not recorded | Echoes the caller's own identity. |
| `GET /metrics` | not recorded | Aggregate request metrics. |
| `GET /tools` | not recorded | Tool schemas only. |
| `GET /ingestion/feature`, `GET /ingestion/migrations` | not recorded | Feature flag and migration versions. |
| `GET /ingestion/jobs/{id}` | not recorded | Status poll: counts and the submitting account, no data-subject data. |

### Agent turns

A turn first checks its purpose (`query` and `ai_call`), so nothing reaches
the model when it would be refused. Each tool the model calls writes its own
`query` event as it completes. The turn then writes one `ai_call` /
`agent.answer` event — the record of what went to the chat model
(`recipient="inference:chat"`): the model alias, a prompt version (language
plus a hash of the system prompt), token counts when every response reported
them, the ids of the turn's tool events as `ai.sources` (the evidence the
model was given), the answer's SHA-256 as `ai.output_sha256`, and as criteria
the message count, a `question_sha256` of the last user message, the
iteration cap, the number of tool calls and whether the turn hit the cap. No
question or answer text is recorded. A turn that fails at the model writes the event with
`outcome="error"`.

## What is recorded: CLI and startup

| Command | Recorded as |
|---|---|
| `python -m chorus.migrations.cli apply` (`make migrate`) | `admin` or `modify` / `migration.apply` per applied migration |
| `python -m chorus.ingestion.cli run` (`make ingest`) | `collect` / `ingest.run`, then `ai_call` / `ingest.ner` when NER ran |
| `python -m chorus.ingestion.cli resolve` (`make resolve`) | the resolution events, as for `POST /ingestion/resolve` |
| `python -m chorus.ingestion.cli backfill-norm-keys` | `modify` / `resolution.backfill_norm_keys` (a no-op writes nothing) |
| `python -m chorus.migrations.cli status` | not recorded: lists migration versions only |
| Backend startup | the chain's start and `config.audit`, then `migration.apply` per applied migration, as the system |

The make targets run the command inside the running backend
(`docker compose exec`), pass the invoking host user as `AUDIT_CLI_USER`, and
take the purpose from `CASE_REF` / `REASON`:

```bash
make resolve CASE_REF=case-0001 REASON="monthly review"
```

A command run that way follows the backend's `AUDIT_REQUIRE_PURPOSE`. None of
chorus's commands queries or discloses, so under the compose default
(`query,disclose`) `make migrate`, `make ingest` and `make resolve` run
without a purpose and record one when it is given. A policy that names one
of their actions — `collect` for an ingestion pass, say — makes the command
exit 2 without `CASE_REF` or `REASON`, before it touches anything.

## Targets and criteria

An event references what it touched by id and count, never by content. No
post text, question, answer, prompt or file content is ever recorded.

- **Tool queries and `/stats`**: `target.kind` is `mixed`, and each id carries
  its kind: `author:<id>` for the people whose data the result discloses
  (authors, co-authors, connected persons), then `entity:<id>` for resolved
  entities, then `post:<uuid>` for posts. `target.count` is the tool's result
  count; criteria are the tool's parameters (entity or author names searched,
  time range, limits).
- **Collection**: `target.kind` is `author`, listing the authors whose data
  entered the store; criteria carry the cutoff and source file names plus the
  per-stage `graph` counts and the per-table `raw_store` counts. A target holds
  one kind and one count, so write events carry their per-kind counts in
  criteria.
- **Combination**: `target.kind` is `entity`, listing the entities minted or
  linked to; criteria carry the run configuration (thresholds, embedding and
  tie-break model aliases) and the per-method `counts`.
- **Transfers to inference** (`ai_call`, bulk stages): one event per stage per
  run with a count (`kind="post"` texts for NER, `kind="alias"` surface forms
  for embedding and tie-break), the model alias, token counts when reported,
  and the operation's event as parent and source — never one event per item.
- **Modification**: migrations reference their version and carry the store's
  change counters (`changes`); the backfill and relink events carry a count of
  aliases.
- **Erasure**: the staged copy of an upload is deleted once its job is over;
  the event names the deleted files (`kind="file"`).
- **More than 200 ids**: the first 200 go on the operation's event, each
  further 200 on a continuation event (same action, operation
  `<operation>.ids`, `parent_event_id` set to the operation's event), so no
  id is cut.
- **Failures**: an operation that fails is recorded with `outcome="error"` and
  what is known (its criteria, the counts reached); exception text never
  goes into an event.

## Fail closed

An audit event that cannot be written fails the operation it records. A
request gets the generic `500` and its data is not returned; inside an agent
turn the failure ends the turn (it is never fed back to the model as a tool
error); a background job is marked failed; a CLI command exits 1. Nothing
falls back to the operational log. Startup stops when the audit stream cannot
be opened. A write is recorded after it is committed, so a collection whose
event fails has still been written; the job reports the failure.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `AUDIT_OUTPUT` | unset (`stdout`); `container` in `docker/compose.yaml` | `stdout`: the process's own stdout. `container`: the container's main stdout under the container-wide lock. |
| `AUDIT_REQUIRE_PURPOSE` | empty; `query,disclose` in `docker/compose.yaml` | Comma-separated actions that need a stated purpose; a request without one is refused with `428`, a CLI run exits 2, and the refusal is recorded. Set it empty (`AUDIT_REQUIRE_PURPOSE=`) to require nothing; unset keeps the compose default. `GET /config` reports it to the SPA as `require_purpose`. Unknown names stop startup. |
| `AUDIT_CLI_USER` | unset | The operator a CLI run is attributed to; the make targets pass the invoking host user. |
| `AUDIT_CASE_REF`, `AUDIT_REASON` | unset | A CLI run's stated purpose; the make targets pass `CASE_REF` / `REASON`. |

## Not recorded, and why

- The endpoints marked *not recorded* above: they return no personal data.
- Graph exports from the explorer (JSON, GraphML, HTML) are built in the
  browser from data an audited tool call already delivered; no request
  reaches the backend (ADR 0016). The tool call's `query` event is the
  record of that disclosure to the caller.
- Evicting finished jobs from the in-memory job registry: job state holds
  counts only, not stored personal data.
- A queued ingest job cancelled at shutdown never runs, so its staged upload
  stays under `CHORUS_HOME/uploads` and no deletion is recorded (see
  ADR 0014 for the orphan-directory caveat).
- Chorus has no other erasure path yet; the retention sweeper
  ([retention.md](retention.md)) will be recorded as `delete` when it lands.

## The legacy SQLite audit log

Releases before 0.8.0 wrote audit rows to `$CHORUS_HOME/audit.sqlite`. Chorus
no longer reads or writes that file; it stays on the `chorus-state` volume,
frozen by its own triggers. How long it is kept, how to read it for a review
and how to delete it: [retention.md](retention.md#legacy-sqlite-audit-log).
