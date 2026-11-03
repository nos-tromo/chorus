# 0018 — Audit events on the federation stream; the SQLite audit log frozen

Status: accepted
Date: 2026-11-04
Supersedes: [0010](0010-resolution-audit-logging.md)
Amends: [0009](0009-agent-tool-calling.md) (agent audit), [0014](0014-frontend-ingestion.md) (audit wiring)

## Context

Chorus kept its own §76 BDSG audit log: an append-only SQLite table on the
`chorus-state` volume, one row per tool call, per agent turn and per resolve
run, with triggers rejecting `UPDATE` and `DELETE`. It had four gaps. It
lived next to the data it audited, on a volume any process in the backend
container could rewrite as a whole. It recorded queries and resolution but
not collection, the other writes, deletion, or what was sent to the
inference models. It had no stated purpose. And its retention setting was
loaded but never enforced.

The federation now provides an audit lane: every app writes hash-chained
audit events (one chain per process, shared library `audit_event.py`) as JSON
lines on stdout; obs-plane's collector routes them to a separate store with
its own retention, access rules and a verifier that checks every chain and
binds it to the container that wrote it. The wire format of a stated purpose
(`X-Audit-Purpose`) and the container writer are shared too
(`audit_runtime.py`).

## Decision

1. **Chorus writes its audit events to the federation stream and nowhere
   else.** The backend opens one chain in its lifespan; every audited CLI
   command opens its own. `@audited` stays the single seam for tools; the
   agent, `/stats`, ingestion, resolution, migrations and the staged-upload
   cleanup write through the same request-scoped context (actor, stated
   purpose, request id). What is recorded, per endpoint and per command, is
   in [audit.md](../audit.md).
2. **Targets reference data subjects first.** Tool and `/stats` events use a
   `mixed` target with kind-prefixed ids — authors, then entities, then posts
   — so a subject-access review can find every query that disclosed a
   person's data by that person's id, without the graph. `posts_mentioning`
   fetches the authors and co-authors of the posts it returns for this
   purpose only.
3. **Bulk transfers are counted per stage, not per item.** NER, embedding and
   the LLM tie-break write one `ai_call` event per stage per run, with counts
   and the model alias, parented to the run's `collect` or `combine` event.
   Write events carry their per-kind counts in criteria, since a target holds
   one kind and one count.
4. **Fail closed.** An event that cannot be written fails the operation: a
   generic 500 with no data, a failed job, a non-zero CLI exit.
5. **The SQLite audit log is frozen, not migrated.** Chorus stops reading and
   writing it; the file stays on the volume, immutable through its own
   triggers. Its rows are kept until the end of the calendar year after the
   last row was written, then deleted by the operator
   ([retention.md](../retention.md#legacy-sqlite-audit-log)). The writer, its
   schema file and its configuration (`AUDIT_DB_PATH`,
   `AUDIT_RETENTION_DAYS`) are removed.
6. **CLI runs go through the running backend.** The make targets use
   `docker compose exec` with `AUDIT_OUTPUT=container`, so a CLI chain lands
   in the backend's log rather than in a one-off container that can exit
   before the collector sees it.

## Alternatives considered

- **Keep the SQLite log and add the stream.** Two records of the same
  operation that can disagree, one of them still rewritable as a file.
  Rejected.
- **Replay the SQLite rows into the stream.** Events are timestamped and
  chained when written; replayed rows would carry the replay's time, or forge
  their own, and break the property that a chain records what happened when
  it happened. Rejected: the frozen file is the record of that period.
- **One event per item for bulk stages** (per post sent to NER, per alias
  resolved). Millions of events per large load, duplicating provenance the
  graph already holds on `:MENTIONS` and `:RESOLVED_TO` edges. Rejected, as
  ADR 0010 did for per-alias rows.

## Consequences

- Positive: one tamper-evident record across the federation; collection,
  modification, erasure and transfers to inference are now recorded; stated
  purposes and request ids are carried; storage, retention and access are
  governed in one place, away from the data.
- Negative: an audit store outage now fails audited operations (by design).
  The legacy file must be read with `sqlite3` for reviews of the period
  before this change, and deleted by hand when its period is over.
- Reversal trigger: none foreseen for the stream itself; if per-item
  transfer records are ever required, the stage counters are the place to
  split.
