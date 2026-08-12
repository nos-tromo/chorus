# 0017 — Project isolation via one Neo4j instance per project

Status: proposed
Date: 2026-08-12

## Context

Chorus is moving toward multi-user operation, where distinct
investigations ("projects") are worked on by different users. Projects
may constitute separate legal processing purposes with their own DSFAs,
retention rules, and access lists, so their data must not mix — for
compliance and security reasons, and because cross-contaminated
datasets silently corrupt analytical results (entity resolution,
co-occurrence, network metrics).

Neo4j Community Edition — our licensed edition — provides neither
multi-database support (everything lives in the single `neo4j`
database) nor role-based access control (one all-powerful DB user).
The database layer therefore cannot provide an isolation boundary at
all: any principal that can reach bolt can read and write everything.
The isolation boundary has to live elsewhere.

Two existing architectural facts shape the solution space:

- State already lives in the separate `data-plane/` compose project,
  precisely so that stateful concerns (backup, retention, access
  policy) sit next to the data (see *Orchestration topology* in
  `CLAUDE.md`).
- Chorus is single-org and single-source (see *Anti-scope*), so the
  number of concurrent projects is bounded and known, not open-ended
  SaaS-style tenancy.

## Decision

Run **one Neo4j Community container per project**, each with its own
data volume, all owned by the `data-plane/` compose project. Isolation
is physical: separate stores, separate page caches, separate bolt
endpoints, separate backups, separate deletion.

Concretely:

- **Data plane.** `data-plane/compose.yaml` gains one Neo4j service +
  volume per project, generated from a template (service name and
  `data-net` alias `neo4j-<project>`). Backup/restore runbooks under
  `data-plane/backup/` operate per project. Ending a project means
  dropping its container and volume — a clean, provable erasure story.
- **Routing.** `chorus/db/neo4j.py`'s driver factory becomes a small
  per-project driver registry, mapping project id → bolt URI. The
  mapping is server-side configuration (env / config file), never
  client input.
- **Authorization.** The trusted-header principal seam
  (`api/auth/principal.py`) is extended with a project claim: the
  gateway/IdP asserts which projects a user may access, and every
  request carries an active-project selection validated against that
  claim. Tool calls, agent queries, `/stats`, and ingestion jobs
  resolve their Neo4j driver from the authenticated principal's active
  project — there is no code path from user input to a bolt URI.
- **Audit.** The §76 BDSG audit logger records the project id on every
  entry, alongside user, parameters, and entities touched.
- **Chorus-side state.** Everything under `$CHORUS_HOME` (audit log,
  raw store, job state, operational logs) is partitioned per project
  (per-project subdirectories on the `chorus-state` volume, or
  per-project volumes). Isolating the graph while sharing the raw
  store would leak upstream rows across projects.
- **Migrations.** `python -m chorus.migrations.cli apply` iterates the
  configured projects; the runner is already idempotent per instance.
  A new project bootstraps by creating its volume + container, then
  applying migrations.
- **Network posture.** Only the chorus backend joins `data-net`; no
  per-project bolt endpoint is published to hosts or other networks.
  Since Community Edition has no DB-level auth worth the name, network
  reachability *is* the DB-layer access control, and the app layer is
  the user-facing one.

Vector indexes come along for free: each instance carries its own
per-project indexes, so `semantic_search` (planned) never ranks
results across projects.

## Alternatives considered

- **Application-layer tenancy in one database** (a `project_id`
  property/label on every node and edge, filtered in every query).
  Chorus is better placed than most apps for this — all Cypher lives
  in version-controlled templates and users never write queries — but
  it is policy isolation, not structural isolation: one missed filter
  leaks data across projects; backups, deletion, entity resolution,
  and vector indexes (not partitionable in Community — top-k runs
  global, then post-filters) remain shared. Hard to defend in a DSFA
  where projects are separate processing purposes. Remains available
  *within* a project later as soft workspaces, but must not be the
  compliance boundary.
- **Neo4j Enterprise Edition.** Multi-database plus real RBAC solves
  the stated problem with the least engineering, but requires a
  commercial license (procurement is its own project in this
  deployment context), and multi-db still shares one JVM and page
  cache — instance-per-project has a strictly smaller blast radius.
  Reconsider if project count outgrows Option A (see reversal
  trigger).
- **Swap the graph database** (e.g. FalkorDB's many-graphs model,
  Kùzu's file-per-database, ArangoDB Community multi-db). ADR 0003
  chose Neo4j for vector + traversal in one Cypher statement; the
  entire query/tool/migration layer is Cypher. A full-pipeline rewrite
  to solve a problem compose files solve.

## Consequences

- Positive: structural (not policy) isolation between projects;
  per-project backup, retention, and erasure; no cross-project
  contamination of resolution or analytics; zero license cost;
  airgap-compatible; per-project vector indexes.
- Negative: RAM cost — each JVM + page cache wants gigabytes, so this
  scales to a bounded handful of projects, not dozens; per-project
  migration runs and health checks; the driver registry, principal
  project claim, and `$CHORUS_HOME` partitioning are real (if modest)
  engineering; `scripts/check_dataplane_health.sh` and `make
  bootstrap` must learn about multiple instances.
- Open dependency: the project claim rides on the OIDC work
  (`principal.py` seam, not yet landed). Until then, dev setups pin a
  single default project the same way `CHORUS_DEFAULT_IDENTITY` pins a
  dev identity.
- Reversal trigger: if the number of concurrent projects grows past
  what host RAM supports (order of ~10+ instances), revisit — first
  candidate is Neo4j Enterprise multi-database (keeps all Cypher and
  the routing seam; the driver registry maps project → database name
  instead of project → instance), second is a graph store with native
  cheap multi-tenancy.
