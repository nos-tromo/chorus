# 0010 — §76 audit logging for the resolution write path

Status: accepted
Date: 2026-05-31
Relates to: [0009](0009-agent-tool-calling.md)

## Context

Every query tool in chorus is wrapped by the `@audited` decorator, which writes
one immutable §76 BDSG audit row per invocation (user, timestamp, params,
entities touched, result count) — see `chorus/audit/logger.py`. The
`Alias → Entity` resolution stage (PR #21) **mints `:Entity` nodes and writes
identity-linking `:RESOLVED_TO` edges with no audit record at all.** A code
review of PR #21 (finding #8, issue #22) flagged this: resolution is arguably the
most sensitive *write* in the system — it creates and links person-entities — yet
left no auditable trail, which sits poorly with chorus's compliance posture
(CLAUDE.md: §76 BDSG, DSFA, "every tool invocation logged"). `compliance.md`
frames §76 as "query logging"; this decision widens it to cover the resolution
write operation.

## Decision

Audit each `resolve_all` run through the **existing** `AuditLogger.time_tool`
seam — no new logger, no schema change. One immutable row per non-empty run:

- `tool_name = "resolve_all"`.
- `params` = the run configuration only (no PII): `embed_cluster_threshold`,
  `llm_tiebreak_enabled`, `case_normalize`, `vector_k`, `embed_model` — enough to
  reconstruct *how* the run behaved.
- `entities_touched` = the distinct entity ids the run minted or attached to (the
  §76 linkage a subject-access-request or reversal review needs).
- `result_count` = aliases processed.
- A failed run is recorded with `status="error"` and the exception message
  (`time_tool` does this automatically), so an aborted run — e.g. the
  embed-count-mismatch `ValueError` — is never a silent gap.

`resolve_all` gains required `audit: AuditLogger` and keyword `user: str`
parameters, mirroring the tool convention `run(driver, params, *, user, audit)`.
There is no `audit=None` escape hatch: mandatory logging is the control. The
`resolve` CLI builds the logger (`AuditLogger(load_audit_env().db_path)` +
`init_schema()`, exactly as `api/main.py`) and resolves the principal with
precedence **`--user` flag → `CHORUS_DEFAULT_IDENTITY` env → `"cli"`** (the same
env the API's `resolve_principal` honors).

Fine-grained per-alias provenance already lives on each `:RESOLVED_TO` edge
(`method`, `score`, `embed_model`, `resolved_at`), so the per-run audit row plus
the per-edge properties together cover audit and reversibility without a second
store.

**Empty run writes no row.** When there are no unresolved aliases, `resolve_all`
returns before entering `time_tool` — nothing happened, so nothing is audited.

## Alternatives considered

- **One audit row per alias resolved.** Finest granularity in the audit log
  itself, but writes potentially millions of rows per run and largely duplicates
  the provenance already on the `RESOLVED_TO` edges. Rejected: cost without
  benefit.
- **Run-summary row + one row per mint.** Bounded volume, highlights entity
  creation specifically. Rejected for v1: more code than the single-row option
  for marginal extra value; the minted ids are already in the run row's
  `entities_touched` and on the edges.

## Consequences

- Positive: the resolution write path now leaves an immutable §76 trail
  (who ran a resolve, under what config, which entities were touched), closing
  the compliance gap; reuses existing infrastructure (no schema change, no new
  logger); failures are audited too.
- Negative / scope: this widens §76 logging from "query logging" to a
  write/ingestion operation — `compliance.md` is updated to note it. The **other
  ingestion writes (the `run` pass: postings/comments/messages/profiles/
  connections) remain unaudited.** Extending audit to the full ingestion pass is
  a reasonable future consistency follow-up but is out of scope here.
- Reversal trigger: if per-alias audit granularity is ever required by a DSFA
  outcome, switch the single-row design for per-alias rows (the per-edge
  provenance already carries the data).
