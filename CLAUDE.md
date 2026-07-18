# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Data confidentiality — hard rule

**NEVER expose actual production or testing data in any file committed or
pushed to git.** This covers not only file contents but also metadata that
references real data: filenames, file descriptions, social-media account
names or handles, user identifications, sample records, log excerpts, and
screenshots. It applies everywhere git sees — source code, tests, fixtures,
docs, examples, configs, commit messages, and CI files. Use fully synthetic,
invented placeholders instead.

# chorus

GraphRAG system for social network analysis. Sister project to `docint`; shares
inference infrastructure but is otherwise independent.

The name comes from many voices forming an analyzable whole — and from the
guitar effect. The latter is not load-bearing.

## Current state

Foundation is up. The app boots, applies Neo4j migrations, serves
`/health`, dispatches seven graph retrieval tools (`posts_mentioning`,
`authors_mentioning`, `author_activity_summary`, `topic_co_occurrence`,
`authors_connected_by_topic`, `network_around`, `social_network_around`)
end-to-end with audit logging, and exposes a natural-language agent
(`POST /agent/query`, ADR 0009) that selects and calls those tools via
OpenAI tool-calling. The two `*_around` tools return `{nodes, edges}`
payloads rendered as interactive `ForceGraph` graphs in the React SPA
(ADR 0015; renderer per ADR 0016), with click-to-expand via two additional
`@audited` tools (`expand_network_node`, `expand_social_node`) and
shift+click/marquee multi-select with batch, view-only node removal
(`@infra/ui#v0.4.0`, ADR 0016 addendum).
The SPA's Landing page is a graph-diagnostics dashboard backed by an
authenticated, §76-audited `GET /stats` endpoint that reports node/edge
counts, top entities and authors, alias-resolution coverage, latest
ingestion timestamp, and a posts-per-platform breakdown. The `Alias → Entity`
resolution stage is implemented (vector clustering + same-type filter + LLM
tie-break, run via `python -m chorus.ingestion.cli resolve`), so the tools
cluster by canonical entity once a resolve pass has run. A frontend ingestion
path (ADR 0014) lets an authenticated user upload CSV exports and run
migrate/ingest/resolve as background jobs from the React SPA ingestion screen
(`POST /ingestion/*`, polled via `GET /ingestion/jobs/{id}`), gated by
`INGESTION_UI_ENABLED` (default off); `make ingest` remains for
bulk/server-side loads. See *Repository conventions* below for the live layout.

`RESPONSE_LANGUAGE=de` flips the whole app to German — agent answers,
entity-query article stripping, and React SPA UI captions (ADR 0013; ADR 0015).
Default is English; the variable lives in the repo-root `.env` so compose
interpolates it into the backend service, which surfaces it to the SPA via
`GET /config`.

Python: `pyproject.toml` accepts `>=3.11,<3.14`; `.python-version`
pins dev to `3.12`. CI runs 3.11/3.12/3.13. The ruff/pyrefly config
mirrors `nos-tromo/.github/configs/python-strict/`; drift fails CI.

Not yet landed (tracked in `docs/decisions/` / open tickets):
- Semantic search — `Post.embedding` backfill + `semantic_search` tool
- Retention sweeper job
- Real OIDC wiring (`principal.py` is the seam)
- `escape_hatch_cypher` power-user tool

## Common commands

```
uv sync                        # install/refresh the venv from uv.lock
uv add <pkg>                   # add a dependency (updates pyproject + lock)
uv run pytest                  # full test suite (integration tests need Docker)
uv run pytest tests/path/test_x.py::test_name   # single test
uv run pytest tests/integration -k <name>       # one integration case
uv run ruff check .            # lint
uv run ruff format .           # format
uv run pyrefly check           # type check
uv run pre-commit run --all-files               # pre-commit hooks

uv run python -m chorus.migrations.cli apply    # apply Neo4j migrations (or: status)
uv run python -m chorus.ingestion.cli run       # one ingestion pass from INGESTION_SOURCE_DIR
uv run python -m chorus.ingestion.cli resolve   # Alias → Entity resolution pass
uv run uvicorn chorus.api.main:app --reload --port 8000   # dev API
```

Integration tests (`tests/integration/`) boot an ephemeral
`neo4j:5.26.26-community` testcontainer — Docker must be reachable, and
the first run pulls the image. The other test dirs are unit tests that
stub inference and need no services.

The Makefile wraps the compose workflow: `make network` / `volumes` /
`build` / `up` / `up-dev` / `dev` (build, then up-dev) / `down` / `migrate` / `ingest` / `resolve` /
`bootstrap` (wait for data-plane health, then up) / `test` /
`pre-commit` / `bundle` (airgap image tarball from the latest annotated
release tag) / `bundle-dev` (airgap tarball of the current working tree, for
dev/soak). `README.md` has the local quick start (env vars, seeding, first
queries).

Production images are built on the internet-connected CI side with
`uv sync --locked` from the hash-locked `uv.lock`, baking the dependency
venv into the image — see *Airgapped operation* for why no
package-manager call in any image may reach the internet.

## Scope

Chorus ingests social media posts from a single defined upstream source within
the organization, extracts entities and relationships, builds a people-centric
knowledge graph, and serves analytical queries over the resulting network.

Primary query types:
- Enumeration ("posts mentioning X in time range Y")
- Network analysis ("authors connected to author A via shared topics")
- Aggregation ("topic distribution over time")
- Semantic similarity ("posts about Z")

Non-technical users should be able to use the structured query UI without
understanding the underlying graph. Power users get an agent-driven natural
language interface.

## Anti-scope

These are **out of scope** and should be pushed back on if they appear in
requirements:

- Document parsing (PDFs, Office, OCR, audio) — that's docint
- Multi-source ingestion — chorus is single-source by design; the unified
  data format is a core architectural advantage, not an accidental constraint
- Public-internet scraping — data comes from the defined upstream system only
- Free-form Cypher generation by end users in the default UI

If multi-source ingestion ever becomes a real requirement, it triggers a
redesign of the ingestion pipeline, not an incremental feature add.

## Tech stack

- **Backend**: Python 3.11+ (3.12 in dev), FastAPI, Uvicorn
- **Frontend**: React SPA (Vite 8 + TypeScript 6 + Tailwind v4, `@infra/ui#v0.1.1`), served by nginx which reverse-proxies the API same-origin (no CORS)
- **Graph DB**: Neo4j Community Edition (5.11+ for native vector indexes)
- **Metadata + audit**: SQLite
- **Entity extraction**: GLiNER, reached through the inference provider
  (LiteLLM-routed task in vllm-service); not in-process in chorus
- **Inference**: shared vLLM / Ollama via `inference-net` Docker network;
  OpenAI-compatible HTTP, provider swappable via env vars
- **Orchestration**: three Docker Compose projects (chorus app, data-plane,
  vllm-service inference). See Orchestration topology below.
- **Graph visualization**: shared `@infra/ui` `<ForceGraph>` (SVG, dependency-free force simulation, no WASM) for the two `*_around` network screens and inline agent graphs, with expand-on-click (ADR 0016)
- **Reverse proxy**: existing Nginx (new vhost for chorus UI); the chorus `frontend` service is itself nginx, reverse-proxying API prefixes to the backend (same-origin, no CORS)

### Invariants

- **Inference is shared, never embedded.** Chorus does not spin up its own
  inference service. Always call existing `inference-net` endpoints.
  Provider selection is env-driven (see Inference provider abstraction
  below).
- **Vectors live in Neo4j**, not in a separate vector store. Vector search
  and graph traversal happen in the same Cypher statement.
- **State lives in the data plane, not in the app stack.** Neo4j and its
  volumes are owned by the separate `data-plane/` compose project. The
  only state chorus keeps (audit log, raw store, operational logs under
  `CHORUS_HOME`) lives in the `chorus-state` volume, declared
  `external: true` — so `docker compose down -v` in chorus can never
  destroy graph data or chorus-local state.
- **Airgapped in production.** Chorus production environments have no
  internet access. This is non-negotiable and shapes every dependency,
  image, and model choice. See Airgapped operation.

Before proposing a change to any architectural choice (graph DB, vector
store, inference topology, ingestion shape), read the relevant file in
`docs/decisions/`. Those records contain the alternatives that were
considered and the conditions under which a reversal would make sense.

## Orchestration topology

Three independent Docker Compose projects, each with its own lifecycle.
Stateful and stateless concerns are kept apart so that app redeploys cannot
touch persistent data, and so that backup, retention, and access-control
policies for stored data live next to the data itself.

```
vllm-service/             # existing, owns the LiteLLM router + vLLM backends
  compose.yaml            # inference endpoints on `inference-net` (alias `vllm-router`)

data-plane/               # owns Neo4j (chorus) and Qdrant (docint) + their volumes
  compose.yaml            # Neo4j reachable as `neo4j` on `data-net`
  backup/                 # backup + restore runbooks live next to the data

chorus/                   # this repo — app only
  docker/compose.yaml
    services:
      backend:            # joins inference-net + data-net; bolt://neo4j:7687
      frontend:           # nginx SPA; internal chorus-net only; reverse-proxies API prefixes → backend:8000
    volumes:
      chorus-state:       # external — audit log, raw store, op logs ($CHORUS_HOME)
```

`docker compose down -v` in the chorus repo must always be safe — graph
data lives in `data-plane/`, and chorus's one volume (`chorus-state`) is
external, so compose never removes it. The worst case is a fast restart.

`make bootstrap` brings the app up in dependency order: create the
networks and the `chorus-state` volume, wait for data-plane health
(`scripts/check_dataplane_health.sh`), then `up`. Inference is assumed
to be already running and is not chorus's responsibility to manage.

## Airgapped operation

Chorus runs in an airgapped production environment with no internet access.
This is a hard architectural constraint, not a deployment-time consideration.
Every dependency, build artifact, and runtime behavior must be evaluated
against it.

### Hard rules

- **No network calls in production code paths.** Any library that fetches
  data, models, configs, or telemetry at runtime is disqualified.
- **All Python dependencies installed into the image at build time via
  `uv`.** On the internet-connected CI side the image build runs `uv sync
  --locked` from the hash-locked `uv.lock`, baking a fully-populated
  virtualenv into the image. The airgapped side loads the prebuilt image
  and runs no package install. No package-manager call in any image reaches
  the internet.
- **Chorus ships no model weights.** All inference — including entity
  extraction — is reached through vllm-service. Chorus mounts no model
  volumes and contains no model files.
- **All container base images mirrored locally.** Dockerfiles reference
  the internal registry. The internet-side CI pulls upstream images,
  retags, and pushes to the internal registry.
- **No telemetry.** Neo4j usage reporting disabled. Any library-level
  telemetry disabled by config or env. Verified during dependency
  review.
- **All inference traffic terminates inside the network.** Calls speak
  OpenAI-protocol HTTP regardless of provider mode and land at
  vllm-service's LiteLLM proxy. Network-level egress controls make
  outbound traffic structurally impossible. See Inference provider
  abstraction.

### Build and delivery flow

```
internet-side CI                        airgapped environment
─────────────────                       ──────────────────────
build images ──────────▶ image tarballs ──▶ load into registry
                                            deploy via compose
```

Model weights are provisioned separately by vllm-service infrastructure —
not chorus CI's responsibility. vllm-service has its own airgap bundling
(`make bundle` in that repo) producing versioned image tarballs; the
chorus delivery stream and the vllm-service delivery stream are
independent and arrive at the airgapped side as separate artifacts.

Concrete expectations:

- `pyproject.toml` + `uv.lock` is the source of truth for dependencies.
  Dependencies are managed via `uv add` / `uv remove`; environments are
  built and refreshed via `uv sync`. `uv.lock` is hash-locked.
- The image build runs `uv sync --locked` from `uv.lock` on the
  internet-connected CI side, installing every dependency into the image's
  virtualenv. The shipped image is self-contained; no package-manager call
  runs on the airgapped side.

### Dependency review

Before adding any dependency, verify:

1. Does it work fully offline after installation?
2. Does it call home (telemetry, version checks, license servers)?
3. Are its transitive deps clean?
4. Is the wheel available on the deployment platforms (architecture,
   Python version)?

Disabled telemetry knobs and non-obvious offline-readiness findings go in
`docs/airgap.md`.

## Data model

Three artifact types share a common `:Post` label and add a specialization
label (`:Posting`, `:Comment`, `:Message`). Common queries — entity mentions,
authorship, semantic search — run against `:Post`. Type-specific queries —
threading, group membership, engagement metrics — run against the
specialization label.

```
Nodes:
  (:Post:Posting   {uuid, network_post_id, url, text, timestamp, timezone,
                    crawled_at, last_updated, location, task,
                    expected_reactions, collected_reactions,
                    expected_comments, collected_comments,
                    system_tags, ingested_at, retention_until, embedding})

  (:Post:Comment   {uuid, network_object_id, url, text, timestamp, crawled_at,
                    replies_count, reactions_count,
                    system_tags, ingested_at, retention_until, embedding})

  (:Post:Message   {uuid, text, timestamp, url, answers_count,
                    system_tags, ingested_at, retention_until, embedding})

  (:Author         {id, handle, vanity_name, display_name, platform,
                    profile_uuid, url, network_object_id, crawled_at,
                    last_updated, profile_type, system_tags, bio,
                    date_of_birth, hometown, work_education,
                    current_city, additional_details})
  (:Entity         {id, canonical_name, type, description, embedding})
  (:Hashtag        {tag})                      # extracted from text body
  (:Platform       {name})                     # network name
  (:Group          {id, name, platform})       # posting groups and chat groups
  (:Attachment     {filename, kind})           # future: audio/video processing
  (:Alias          {surface_form, norm_key})   # entity resolution history

Edges:
  (Author)-[:AUTHORED]->(Post)
  (Author)-[:CO_AUTHORED]->(Posting)
  (Author)-[:QUOTED_IN]->(Posting)             # quoted user — no quoted post ref
  (Comment)-[:ON]->(Posting)                   # comment belongs to posting
  (Comment)-[:REPLIES_TO]->(Comment)           # threaded comment replies
  (Message)-[:IN_CHAT]->(Group)
  (Message)-[:REPLIES_TO]->(Message)           # chat thread
  (Posting)-[:IN_GROUP]->(Group)
  (Post)-[:ON_PLATFORM]->(Platform)
  (Post)-[:MENTIONS {confidence, span_start, span_end, model_version}]->(Alias)
  (Post)-[:HAS_HASHTAG]->(Hashtag)
  (Post)-[:HAS_ATTACHMENT]->(Attachment)
  (Author)-[:FOLLOWS]->(Author)                # directed: "A follows B"
  (Author)-[:FRIENDS_WITH]->(Author)           # queried as undirected
  (Alias)-[:RESOLVED_TO]->(Entity)
```

### Design notes

- **UUID is the primary key.** All graph keys for posts/comments/messages are
  the upstream UUID. Network-side IDs are kept as properties for traceability
  but never used as identifiers within chorus.
- **Multi-label Post pattern.** `MATCH (p:Post)` matches all three artifact
  types; `MATCH (p:Comment)` matches only comments. Cypher tools should use
  the narrowest applicable label.
- **Aliases are nodes**, not properties on Entity. Resolution history stays
  queryable and reversible; bad merges can be undone without losing the
  original surface form. Resolution stamps a normalized `norm_key`
  (trim + casefold) on resolved aliases so case variants cluster durably
  across runs (ADR 0012, migration 004).
- **`MENTIONS` targets the `:Alias` surface form, not `:Entity`.** Extraction
  writes `(:Post)-[:MENTIONS]->(:Alias {surface_form})`; resolution later adds
  `(:Alias)-[:RESOLVED_TO]->(:Entity)`. "A post mentions an entity" is therefore
  the two-hop path `Post → Alias → Entity`, which the graph tools collapse with a
  `coalesce(entity, alias)` rule (entity when resolved, else the surface form).
  The edge carries provenance (span offsets, model version, confidence) so
  re-extraction with a newer model is auditable.
- **`retention_until` on Post** drives nightly cleanup (sweeper not yet
  landed). Cascade behavior is defined explicitly — see `docs/retention.md`.
- **Embeddings live on nodes** (`Post.embedding`, `Entity.embedding`), indexed
  via Neo4j vector indexes. No separate vector store.
- **`system_tags` vs hashtags.** `system_tags` is the upstream `Tags` field
  (categorical labels supplied by the source system) stored as a string array
  property. Hashtags are extracted from `Text Content` and modeled as nodes
  for co-occurrence analysis. Do not conflate them.
- **Social graph: directed vs undirected.** `[:FOLLOWS]` is directed —
  always read as "source follows target." `[:FRIENDS_WITH]` is stored once
  per pair (Neo4j edges are physically directed) and queried without
  direction in Cypher (`MATCH (a)-[:FRIENDS_WITH]-(b)`). Do not duplicate
  friendship edges in both directions; the ingestion layer is responsible
  for picking a stable canonical direction (e.g. lower UUID → higher UUID).
- **Author profile enrichment.** The artifact stages create thin
  `:Author` nodes (`id`, `handle`, `display_name`, `platform`). The
  `profiles` upstream table is the authoritative source for author
  identity and enriches `:Author` with the remaining properties,
  including personal fields (`bio`, `date_of_birth`, …). That personal
  data is retained indefinitely — the retention sweep does not touch
  `:Author`. See ADR 0006 and `docs/compliance.md`.

## Upstream data format

Chorus ingests several table formats from the upstream system: the three
post artifacts (`postings`, `comments`, `messages`), a `profiles` table,
and a `connections` social-graph table. **UUID** is the canonical
identifier for the post artifacts and the primary key into chorus — not
the network-side post/comment/message ID. The `profiles` table is the
exception: it joins on the network author `ID` (see below).

### `postings` — top-level posts

```
UUID, Posting ID, URL, Date last updated, Timestamp, Timezone, Crawled at,
Postings Connections, Network Posting ID, Location, Author ID, Author,
Vanity Name, Co-Author, Quoted User, Expected Reactions, Collected Reactions,
Expected Comments, Collected Comments, Network, Posted in Group, Task,
Text Content, Filename, Tags
```

Maps to `(:Post:Posting)`. `Author` and `Co-Author` resolve to `(:Author)`
nodes via `[:AUTHORED]` and `[:CO_AUTHORED]`. `Posted in Group` maps to
`(:Group)` via `[:IN_GROUP]`. `Filename` is the multimedia attachment hook
captured as `(:Attachment)` but not content-processed in v1.

### `comments` — replies to postings

```
UUID, Comment ID, Network Object ID, URL, Crawled at, Network, Text Content,
Timestamp, Tags, Author ID, Author, Vanity Name, Replies Count,
Reactions Count, Parent Comment Text, Parent Comment ID, Posting Text,
Posting ID
```

Maps to `(:Post:Comment)`. `Posting ID` resolves to the parent
`(:Post:Posting)` via `[:ON]`. `Parent Comment ID`, when present, resolves to
another `(:Post:Comment)` via `[:REPLIES_TO]`. `Parent Comment Text` and
`Posting Text` duplicate parent content in the source row — these are useful
as extraction context during ingestion but **not** stored on the comment
node (the parent's text is already on the parent node).

### `messages` — chat messages

```
UUID, Chat ID, Sender, Timestamp, Text, Tags, URL, Chat Group,
Answers Count, Reply To, Network
```

Maps to `(:Post:Message)`. `Sender` normalizes to an `(:Author)` node — same
label as posting/comment authors, no separate "sender" type. `Chat ID` +
`Chat Group` map to a `(:Group)` node via `[:IN_CHAT]`. `Reply To` resolves
to another `(:Post:Message)` via `[:REPLIES_TO]`.

### `profiles` — author profiles

```
UUID, ID, URL, Network Object ID, Crawled at, Date Last Updated, Name,
Vanity Name, Profile Type, Target Profile, Profile Owner, Groups,
Postings, Co Author of Postings, Quoted in Postings, Chat Messages,
Media Items, Comments, Friends, Connected Users, Tags, Network, Bio,
Date of Birth, Hometown, Work/Education, Current City, Additional Details
```

Author-profile enrichment, **not** a social graph — one row per author
profile. Each row enriches the existing `(:Author)` node; the join key is
`ID` (the network author id, equal to the `Author ID` used as
`:Author.id`), not `UUID` — `UUID` is kept as the `profile_uuid`
property. Identity and personal fields (`Name`, `Vanity Name`,
`Profile Type`, `Bio`, `Date of Birth`, `Hometown`, `Work/Education`,
`Current City`, …) become `(:Author)` properties.

The relationship and aggregate columns (`Friends`, `Connected Users`,
`Postings`, `Comments`, `Groups`, `Media Items`, `Co Author of Postings`,
`Quoted in Postings`, `Chat Messages`) are denormalized duplicates of
edges the artifact tables and the `connections` edge table already own —
they are **not** mapped to the graph; the raw store keeps the full row.
`Target Profile` and `Profile Owner` have unclear semantics and are
deferred (raw store only). See ADR 0006.

### `connections` — social graph

```
Account Linking, Name, Vanity Name, Groups, Postings, Co Author of Postings,
Quoted in Postings, Chat Messages, Media Items, Comments, Friends,
All Connected Users, Tags, Connections, Vanity Name selected conn. User,
Network Object ID selected conn. User, Posting Conn., Comment Conn.,
Reaction Conn., React. Like, React. Love, React. Haha, React. Wow, React. Sad,
React. Angry, ChatMessage Conn., Media Conn., Friend, Follower, Following,
Network Object ID, Crawled at, Profile Type, Url, Network, Target-Profile?,
Hometown, Current City, Date of Birth, Place of Work/Education, Bio,
Additional Details
```

Each row describes one connected user with respect to a constant
target (the "selected conn. User" columns). A single `connections.csv`
file may carry rows for many targets concatenated; each row stands on
its own. The vendor groups this and the `profiles` table above under
a single "connections" label, but only this table is the social
graph; the two are ingested as separate modules (see ADR 0006).

**Edge dispatch** (see ADR 0007):

- `Follower=Yes` → `(row_user)-[:FOLLOWS]->(target)`.
- `Following=Yes` → `(target)-[:FOLLOWS]->(row_user)`.
- `Friend=Yes` → `(a)-[:FRIENDS_WITH]->(b)` where `a.id < b.id`
  lexicographically — canonical direction picked at the DTO so
  re-emission from either orientation dedupes by MERGE.

Flags coexist on a single row: mutual follow → both `Follower=Yes`
and `Following=Yes`, producing two `:FOLLOWS` edges. A row with all
three flags `No` carries no signal and is dropped at `from_row`
(self-loops on `Network Object ID` likewise).

**Edge properties.** Only `crawled_at` in v1. The per-pair engagement
columns (`Posting Conn.`, `React. Like`/`Love`/`Haha`/`Wow`/`Sad`/
`Angry`, `Comment Conn.`, `ChatMessage Conn.`, `Media Conn.`) are
preserved verbatim in the raw store but **not** projected to the
graph — they are derivable from postings/comments/reactions once those
land. Adding them as edge properties later is a non-breaking
migration.

**Owner identity columns** (`Name`, `Vanity Name`, `Bio`, `Hometown`,
…) duplicate `profiles.csv` and are written with `ON CREATE SET` only
— profiles remains authoritative per ADR 0006. The denormalized
aggregate columns on the row user (`Postings`, `Comments`, `Friends`,
`Connections` counts, …) are not mapped — they are derivable from
the graph.

**Re-crawl semantics.** Snapshot-additive: edges MERGE idempotently,
`crawled_at` updates to the latest encounter via `SET`. Removed
relationships are not detected; this is a known limitation. See
ADR 0007.

**Volume and indexing.** Connections may dwarf the artifact tables in
row count for organizations with rich social graphs. The writer
batches via `UNWIND` in chunks of 500 DTOs; `Author.id` is uniquely
constrained (migration 001) and `:FOLLOWS` / `:FRIENDS_WITH` are
indexed on `crawled_at` (migration 002), so the bulk load is
index-backed from the first row.

**Unknown authors.** Connections rows commonly reference authors who
never appear in the post/comment/message tables (a follower who
doesn't post). The writer MERGEs thin `:Author` nodes for them so
multi-hop traversal queries find structurally complete paths.

### Field semantics worth pinning down

- **UUID** is the only identifier guaranteed unique and stable within chorus.
  Network-side IDs (`Posting ID`, `Comment ID`, `Network Posting ID`,
  `Network Object ID`) are kept as properties for upstream traceability but
  never used as graph keys.
- **Timestamps**: `Timestamp` is content creation time, `Date last updated`
  is content edit time, `Crawled at` is the *upstream* crawl time. All three
  are optional and informational — a missing/blank value is kept as `None`,
  never dropping the row. Retention is **not** measured from any of them:
  every artifact gets a chorus-set `ingested_at` (the time chorus ingested it,
  one value per run) and `retention_until` runs off that, uniformly across
  postings, comments, and messages (ADR 0011). `RETENTION_ENABLED=false`
  disables retention entirely (no `retention_until` is written). See
  `docs/retention.md`.
- **Expected vs Collected reactions/comments** signal known crawl
  incompleteness. Store both. Any analytical output that aggregates
  engagement must surface the delta so users see the uncertainty rather than
  treating collected counts as authoritative.
- **`Tags`** (upstream field) ≠ hashtags. Stored as `system_tags` string
  array property on Post nodes. Hashtags extracted from `Text Content` are
  separate `(:Hashtag)` nodes.
- **`Quoted User`** on postings is a user reference only, not a post
  reference. The graph cannot represent "this posting quotes that posting" —
  only "this posting quotes this user." Document this limitation in any
  analytical output that touches quoting.
- **`Vanity Name`** is platform-specific (e.g. LinkedIn slug). Stored on
  `(:Author)`; useful for resolution but not authoritative across platforms.
- **`Network`** is the platform name. Resolves to `(:Platform)` via
  `[:ON_PLATFORM]`. Same value across all three artifact types.

### Multimedia handling (future)

`Filename` on postings is the only field referencing non-text payloads.
For the v1 prototype, attachments are recorded as `(:Attachment {filename})`
nodes linked via `[:HAS_ATTACHMENT]` with no content extraction. Future
work:

- **Audio**: transcription through vllm-service's `/v1/audio/transcriptions`
  endpoint, called via the same provider abstraction as completions and
  embeddings. Requires vllm-service to be started with `--profile media`.
  Transcripts become additional text on the parent posting for entity
  extraction and embedding — no separate node type.
- **Video**: audio track transcribed as above; visual entity detection on
  frames is a further future step, scope TBD.

Until that work lands, the ingestion pipeline must not silently drop the
filename — record the attachment node so it can be backfilled later.

## Retrieval pattern

The agent does **not** write Cypher. High-value queries are wrapped as named,
parameterized tools, with the Cypher in version-controlled template files
under `queries/`. The agent selects tools and parameters; Cypher stays under
human control and auditable.

Implemented tool set (self-registered in `chorus/tools/`, served at
`/tools`, callable by the agent):

- `posts_mentioning(entity, time_range)` — graph filter
- `authors_mentioning(entity, time_range)` — entity → author leaderboard
- `author_activity_summary(author, time_range)`
- `topic_co_occurrence(entity, hops)`
- `authors_connected_by_topic(seed_author, min_overlap, max_hops)`
- `network_around(entity, depth, limit)` — entity neighborhood, for
  visualization
- `social_network_around(author, depth, limit)` — author ego network over
  `:FOLLOWS` / `:FRIENDS_WITH`, for visualization

Planned: `semantic_search(query, k, filters)` (vector index on
`Post.embedding`), and `escape_hatch_cypher(query)` for power users —
behind a permission flag, **not** exposed in the default UI, calls
logged with full Cypher text.

### Adding a graph tool

A new tool touches six files; registration alone surfaces it to both the
REST `/tools` surface and the NL agent (each iterates the `TOOLS`
registry):

1. `chorus/queries/<tool>.cypher` — the query (never inline in Python)
2. `chorus/tools/<tool>.py` — Pydantic in/out models + `@register_tool`
   + `@audited`. The function's first docstring line becomes the
   agent-facing description (enforced by `tests/tools/test_registry.py`);
   audit metadata flows via `audit_entities()` / `audit_result_count()`
   on the output model — keep audit-only data on a `PrivateAttr`.
3. `chorus/tools/__init__.py` — import the module so it self-registers
4. `tests/conftest.py` — add the module to `_CHORUS_ENV_MODULES`; easy
   to miss, and without it the tool silently drops out of the registry
   after the per-test module reload
5. **React SPA screen** — three cases:
   - *Standard table tool* (returns a flat list): add a `ToolSpec` entry
     to `frontend/src/tools/specs.ts` and a route entry in
     `frontend/src/routes/Router.tsx` + the matching Sidebar nav item in
     `frontend/src/layout/Sidebar.tsx`. The generic `<ToolScreen>` renders
     the form and `<DataTable>` for you.
   - *Bespoke graph tool* (`network_around`, `social_network_around`): write
     a dedicated route component (e.g. `frontend/src/routes/ToolNetwork.tsx`),
     add it to the router and sidebar, wire it through `useGraphExplorer` for
     expand-on-click state, and add `ForceGraph` element/style mappers in
     `frontend/src/lib/` (e.g. `networkElements.ts`) (ADR 0016).
   - *Bespoke tabular tool* (custom result shape, non-graph): write a
     dedicated route component (e.g. `frontend/src/routes/ToolAuthorActivity.tsx`),
     add it to the router and sidebar. No element mappers needed.
6. `tests/integration/test_<tool>.py` — per-tool tests live in
   `tests/integration/` (`tests/tools/` holds only the registry test)

## Ingestion pipeline

```
upstream system
    │
    ▼
pull adapter (single, thin interface)  ──▶ SQLite raw store
    │
    ▼
GLiNER extraction
    │
    ▼
entity normalization (case, handles, alias table)
    │
    ▼
embedding-cluster unresolved spans
    │
    ▼
LLM tie-breaker for ambiguous merges
    │
    ▼
Neo4j (graph + embeddings)
    │
    ▼
SQLite audit log
```

The pull adapter is the only thing that knows about the upstream system's
schema. Keep it isolated. If a second source ever appears, write a second
adapter — do not generalize the existing one.

Entity resolution thresholds are config, not constants.

## Repository conventions

Chorus is a Python package. Everything under `chorus/` is importable code;
infrastructure (Docker, Make, CI) and prose (`docs/`, `tests/`) sit
alongside at repo root.

```
chorus/                      # top-level repo
  chorus/                    # the importable package
    __init__.py
    agent/
      loop.py                # NL agent: OpenAI tool-calling loop over TOOLS (ADR 0009)
      openai_tools.py        # TOOLS registry → OpenAI tool schemas
      prompts.py
    api/
      main.py                # FastAPI entrypoint (lifespan: logger → driver → migrations → audit)
      auth/principal.py      # trusted-header principal seam (OIDC swap-in)
      routers/               # health.py, config.py, tools.py, agent.py, ingestion.py
    audit/
      logger.py              # §76 BDSG audit log (SQLite, append-only, trigger-enforced)
      schema.sql
    db/
      neo4j.py               # driver factory + session() context manager
    inference/
      provider.py            # OpenAI client; chat/embed/rerank by `model` field
      ner_client.py          # GLiNER /gliner HTTP client (decoupled from `provider`)
    ingestion/
      adapter.py             # UpstreamAdapter Protocol — the only place that knows the upstream schema
      upstream.py            # concrete adapter
      postings.py / comments.py / messages.py     # per-table DTOs + write functions
      profiles.py            # author-profile enrichment → :Author (ADR 0006)
      connections.py         # node-edge-node edge table: :FOLLOWS / :FRIENDS_WITH (ADR 0007)
      orchestrator.py        # stage runner; inline NER per post (NER_ENABLED gates)
      extraction.py          # ner_client.extract_entities → :MENTIONS with provenance
      resolution.py          # alias / embed-cluster / LLM tiebreak
      raw_store.py           # separate SQLite, not the audit DB
      jobs.py                # background job registry for the ingestion UI (ADR 0014)
      cli.py                 # python -m chorus.ingestion.cli {run,resolve}
    migrations/
      runner.py              # idempotent applier, tracked via (:_Migration {version})
      cli.py                 # python -m chorus.migrations.cli {apply,status}
      NNN_*.cypher           # one file per migration; ${EMBED_DIM} substituted at apply time
    queries/                 # Cypher templates, one file per tool — never inline
      posts_mentioning.cypher
      ...
    tools/                   # @audited Python wrappers around queries
      _template_loader.py
      _audit.py              # @audited + register_tool + TOOLS registry
      posts_mentioning.py
      ...
    utils/
      env_cfg.py             # every env var loader as a frozen dataclass
      logger_cfg.py          # loguru sinks (stderr + rotating file)
  frontend/                  # React SPA (ADR 0015) — Vite + TypeScript + Tailwind v4
    index.html
    package.json + pnpm-lock.yaml
    vite.config.ts           # dev proxy of API prefixes → :8000
    tsconfig.json / vitest.config.ts
    nginx/
      default.conf.template  # env-templated upload limit (CHORUS_CLIENT_MAX_BODY_SIZE)
      security-headers.conf  # hardened CSP
    src/
      api/                   # client.ts (no identity header — proxy sets X-Auth-User), queryClient.ts, types.ts, per-domain modules
      config/                # ConfigProvider — boots GET /config (language + ingestion_enabled)
      i18n/                  # typed en/de catalog (~160 keys) + useT() hook; parity test
      layout/                # Shell.tsx, Sidebar.tsx
      routes/                # Router.tsx + one screen per route (Agent, Ingestion, tool screens)
      components/            # DataTable, AgentGraphCard (inline ForceGraph), ToolTrace, ToolScreen, ...
      hooks/                 # useHealth, useTools, useToolCall, useAgentQuery, useGraphExplorer, ingestion hooks
      lib/                   # networkElements.ts, socialElements.ts, graphExplorer.ts (ForceGraph element/style mappers + merge logic, ADR 0016)
      config/                # ConfigProvider, useConfig(), useT() i18n hook
      tools/                 # specs.ts — ToolSpec declarations for the generic table-tool screens
  tests/                     # unit dirs mirror chorus/; per-tool tests in tests/integration/
  docker/
    Dockerfile.backend / Dockerfile.frontend   # both base images pinned by @sha256: digest
    compose.yaml             # app services only — the lone volume (chorus-state) is external
    compose.override.yaml    # dev overlay: publishes backend + frontend (nginx:80) host ports
  docs/
    architecture.md / retention.md / compliance.md / airgap.md
    decisions/               # ADRs, one file per significant decision
  scripts/                   # bundle_images.sh, check_dataplane_health.sh
  Makefile                   # network / volumes / build / up / bundle / migrate / ingest / resolve / bootstrap / frontend-lint / frontend-test
  pyproject.toml + uv.lock + pytest.ini
  .pre-commit-config.yaml
  .github/workflows/ci.yml   # delegates to the shared nos-tromo python-app-ci workflow; frontend job: lint + typecheck + test
```

### Rules of thumb

- Cypher lives in `queries/`, never inline in Python. Tools import templates.
- Every retrieval tool has a Pydantic input schema and a typed return.
- Every tool invocation goes through the audit logger before execution.
- Schema changes are migrations, applied in order, idempotent.
- Decision records (the "we chose X over Y because Z" entries) go in
  `docs/decisions/` as short markdown files. New significant tradeoffs
  prompt a new decision record.

## Development tooling

- **Package management**: `uv`. Used for development environments, lockfile
  generation, and Docker builds. `pyproject.toml` + `uv.lock` are the
  source of truth — no hand-edited `requirements.txt` checked in.
- **Tests**: `pytest`. Neo4j-dependent tests live in `tests/integration/`
  and run against an ephemeral `neo4j:5.26.26-community` testcontainer
  (session-scoped; Docker required). Unit tests stub the inference
  provider via the `fake_inference` fixture (chat, embed, rerank, NER).
  Several chorus modules snapshot env vars at import time, so
  `tests/conftest.py` reloads the modules in `_CHORUS_ENV_MODULES` per
  test — env-snapshotting or self-registering modules must be on that
  list, and env-free ones (e.g. `ingestion.jobs`) must stay off it, or
  class identities duplicate and `except`/`isinstance` checks break.
- **Logging**: `loguru`, same configuration pattern as docint —
  structured output, JSON in production, human-readable in dev.
  Operational logging and the §76 BDSG audit logger are separate
  concerns; do not conflate them in code or in storage.
- **Type checking**: `pyrefly`. Pydantic models for all tool I/O and
  ingestion DTOs so pyrefly stays useful at module boundaries.
- **Lint and format**: `ruff` (single tool for both — no separate
  `black`).
- **Pre-commit**: `pre-commit` runs ruff and pyrefly on changed files
  before commit. Full pytest runs in CI, not in the hook.
- **CI**: GitHub Actions. `.github/workflows/ci.yml` delegates to the
  shared `nos-tromo/.github` python-app-ci workflow (pinned tag):
  ruff → pyrefly → pytest across 3.11/3.12/3.13 (`uv sync --frozen`) →
  Docker build. The airgap delivery bundle is `make bundle` (versioned
  image tarballs via `scripts/bundle_images.sh`). No CI-driven deploy in
  v1; deploys are manual on the airgapped side via the data-plane and
  chorus compose projects.

## Inference provider abstraction

Mirror docint's pattern. The `inference/provider.py` module exposes a single
OpenAI-compatible client constructed from environment variables, matching the
conventions vllm-service consumers use:

```
INFERENCE_PROVIDER=vllm|ollama|openai
OPENAI_API_BASE=http://vllm-router:4000/v1   # picked up by the OpenAI SDK
OPENAI_API_KEY=<token>

# per-task model IDs (LiteLLM routes by the `model` field in each request)
TEXT_MODEL=...
EMBED_MODEL=...
RERANK_MODEL=...
```

No code outside this module references provider specifics. Swapping providers
is an env change.

All chat/embed/rerank traffic is OpenAI-protocol HTTP regardless of provider
mode. In production, every request lands at vllm-service's LiteLLM proxy
(`http://vllm-router:4000/v1` in-network), which routes by the `model` field
in each request body. Currently active routed tasks are `chat`, `embed`,
`rerank` (always on), plus `audio` and `translate` (media profile only). The
proxy is the single ingress for inference traffic, and vllm-service sits
behind network-level egress controls, so outbound traffic is structurally
impossible. The `openai` provider mode points at this local proxy, not at
`api.openai.com` — which is what makes it safe to use in production as well
as in development.

NER is the one task that does **not** go through `provider.py`. GLiNER on
vllm-service is a Ray Serve pass-through with the GLiNER-native
`{text, labels, threshold}` shape on `/gliner`, not a model-field-routed
task. `inference/ner_client.py` owns that endpoint and is configured with
its own env-var family (`NER_API_BASE`, `NER_API_KEY`, `NER_THRESHOLD`,
`NER_TIMEOUT`). Keeping NER decoupled from `INFERENCE_PROVIDER` lets
chorus mix providers — e.g. Ollama for chat/embed/rerank on a dev Mac,
while NER still reaches vllm-service's ner-only stack. Two deployment
shapes are supported by the same code:

- Full vllm-service stack: `NER_API_BASE=http://vllm-router:4000` with
  Bearer auth (`NER_API_KEY=$OPENAI_API_KEY`).
- ner-only stack: `NER_API_BASE=http://gliner-ner:8000`, no auth.

## Compliance posture

Chorus processes data that is materially more sensitive than docint's
administrative documents — observation of behavior, third-party data, likely
Art. 9 categories. This shapes several defaults:

- **Authentication required from v1.** OIDC against the organizational IdP.
  Not retrofitted later.
- **§76 BDSG query logging from day one.** Every tool invocation logged with
  user, timestamp, parameters, entities touched, result counts. Immutable
  table, separate retention from content data.
- **Per-post retention timers.** `Post.retention_until` set at ingestion.
  Nightly cleanup hard-deletes expired content.
- **DSFA is required**, scoped specifically to social network analysis for
  the defined organizational purpose. Narrow scope > flexible platform.

Detailed compliance design lives in `docs/compliance.md`.

## Relationship to docint

Chorus and docint are independent applications that share infrastructure:

- Same `inference-net` Docker network
- Same vLLM and Ollama endpoints
- Same GLiNER service (reached via vllm-service's LiteLLM proxy)
- Same Nginx reverse proxy
- Same operational conventions (compose federation, healthchecks,
  env-driven config)

They do **not** share:

- Storage layers (chorus has its own Neo4j, its own SQLite)
- Entity vocabularies (independent canonical entity stores in v1)
- Codebases (separate repos, separate release cycles)
- DSGVO assessments (separate DSFAs)

If a future need arises for cross-app entity linking (e.g. matching a person
named in an administrative document to their social posts), expose chorus's
entity resolution layer as a FastAPI service and call it from docint. Do not
merge the codebases.
