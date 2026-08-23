# chorus documentation

This directory is the in-repo reference material for **chorus**, the GraphRAG
system for social network analysis. It complements the top-level
[`README.md`](../README.md) (which covers install and the local quick start)
and [`CLAUDE.md`](../CLAUDE.md) (the canonical design: data model, scope,
upstream format, invariants) with topic-by-topic detail.

## Table of contents

| Document | What it covers |
|---|---|
| [tutorial-first-queries.md](tutorial-first-queries.md) | Guided first pass: the tool registry, seeding a row into an empty graph, invoking a tool, asking the agent |
| [architecture.md](architecture.md) | Runtime surface, the frontend tier and SPA bootstrap, the auth seam, the data-plane and inference contracts, observability |
| [ingestion.md](ingestion.md) | Running the ingestion and alias-resolution stages: CLI, env knobs, thresholds, the make and UI paths |
| [retention.md](retention.md) | `retention_until` timers, the nightly sweep, and the cascade rules for expiry |
| [compliance.md](compliance.md) | §76 BDSG audit logging across the query tools and the resolution write path, profile-data retention, and the v1 compliance posture |
| [airgap.md](airgap.md) | What the airgapped production constraint implies for dependencies, images, and inference |
| [decisions/](decisions/) | ADRs — one file per load-bearing architectural choice, with the alternatives considered |

Design history (dated plan and proposal files) lives alongside these in this
directory; it records how a decision was reached and is not maintained as
current reference.

## Who this is for

- **New to chorus** — run the quick start in [`README.md`](../README.md),
  then work through [tutorial-first-queries.md](tutorial-first-queries.md).
- **Operators** loading data and running the stack — start with
  [ingestion.md](ingestion.md), then [architecture.md](architecture.md) for
  the deployment topology and [retention.md](retention.md) for expiry.
- **Backend developers** adding a graph tool or touching the pipeline — read
  *Adding a graph tool* and *Data model* in [`CLAUDE.md`](../CLAUDE.md), then
  the relevant record in [decisions/](decisions/) before changing anything
  load-bearing.
- **Compliance and security reviewers** — [compliance.md](compliance.md) and
  [airgap.md](airgap.md), plus ADR 0010 (resolution audit logging) and
  ADR 0011 (retention anchored on `ingested_at`).

## Conventions used in these docs

- **Source references** are repo-relative paths (for example
  `chorus/queries/posts_mentioning.cypher`, `docker/compose.yaml`) — never
  absolute paths from a development machine.
- **ADRs** are numbered `NNNN-slug.md` in [decisions/](decisions/) and are
  referred to in prose as "ADR 0009" — the number is stable, the slug is not.
- **Cypher** shown in these docs mirrors the templates under
  `chorus/queries/`; queries are never inlined in Python.
- Documentation is plain Markdown (GitHub Flavored). No docs build step.
