# Audit logging for the resolution stage — design

**Date:** 2026-05-31
**Status:** approved (design); pending implementation plan
**Issue:** #22 (code-review finding #8 from PR #21)
**Branch:** `feat/resolution-audit-logging`, off `main` (PR #21 merged as `9145848`)

## Context

The `Alias → Entity` resolution stage (PR #21) mints `:Entity` nodes and writes
identity-linking `:RESOLVED_TO` edges with **no §76 audit logging**, while every
query tool goes through the `@audited` logger. chorus's compliance posture
(CLAUDE.md: §76 BDSG, DSFA, "every tool invocation logged with user, timestamp,
parameters, entities touched"; ADR 0008's identity-resolution scope) makes the
entity-minting/linking path — arguably the most sensitive *write* in the system —
a real audit gap. This adds an immutable audit record of each resolution run.

## Existing seam (reused, no new infrastructure)

`chorus/audit/logger.py::AuditLogger.time_tool(user, tool_name, params)` is a
context manager that writes **exactly one immutable row** on exit (even on
exception: `status="error"` + captured message). It yields a `_Slot` with
`entities_touched`, `result_count`, `status`, `error_message`. The `audit_log`
schema columns already fit a resolution run — **no schema change required**,
which is the signal that the right design reuses `time_tool` rather than adding a
parallel table. The API builds the logger in two lines
(`AuditLogger(load_audit_env().db_path)` + `init_schema()`, `api/main.py`); the
`resolve` CLI does not yet construct one.

## Approved decisions

- **Granularity: one audit row per `resolve_all` run** (not per-alias, not
  per-mint). Mirrors how an agent turn is one parent row. Per-alias provenance
  already lives on each `RESOLVED_TO` edge (`method/score/embed_model/resolved_at`),
  so per-run audit + per-edge provenance together cover SAR/reversal without
  writing millions of audit rows.
- **Wiring: required `audit` + `user` params on `resolve_all`** (mirrors the tool
  convention `run(driver, params, *, user, audit)`). No `audit=None` escape hatch
  — mandatory logging is the compliance control. The CLI constructs the logger
  and passes the principal.

## Design

### Signature change

```python
def resolve_all(driver: Driver, cfg: ResolutionConfig, audit: AuditLogger, *, user: str) -> ResolutionSummary:
```

Breaking change to the single caller (the `resolve` CLI) and the `resolve_all`
tests — all updated in this PR.

### What the row records

Wrap the body of `resolve_all` in:

```python
with audit.time_tool(user, "resolve_all", params) as slot:
    ...                      # existing fetch + embed + per-alias loop
    slot.entities_touched = sorted(touched)   # distinct entity ids minted/attached
    slot.result_count = counts["processed"]
    return ResolutionSummary(**counts)
```

- `params` = run config only (no PII): `{embed_cluster_threshold,
  llm_tiebreak_enabled, case_normalize, vector_k, embed_model}` — lets an auditor
  reconstruct *how* the run behaved.
- `slot.entities_touched` = the distinct entity ids the run minted or attached to,
  accumulated in the loop (`resolve_alias_to_entity` already returns
  `(entity_id, method)`; the cache path has the id too). This is the §76
  "entities touched" linkage an SAR/reversal review needs.
- `slot.result_count` = aliases processed this run.
- **Failure is audited too:** `time_tool` writes the row with `status="error"`
  and the message if the block raises — including the embed-count `ValueError`
  (finding #6). The early `if not aliases: return ResolutionSummary()` path
  returns *before* entering the `with`, so an empty run writes **no** row (nothing
  happened) — acceptable and intentional.
- Per-method counts stay in the `logger.info` operational line and the returned
  `ResolutionSummary`; the audit row is the immutable compliance record.

### Collecting `entities_touched`

In the loop, add resolved ids to a `set[str]`:
- mint/attach branch: `touched.add(entity_id)` (from `resolve_alias_to_entity`'s return).
- cache branch: `touched.add(run_cache[cache_key])`.

Assign `slot.entities_touched = sorted(touched)` before the return.

### CLI

`chorus/ingestion/cli.py` `resolve` branch:
- Add a `--user` argument to the `resolve` subparser (`default=None`).
- Resolve the principal with explicit precedence: **`--user` flag → else
  `CHORUS_DEFAULT_IDENTITY` env → else `"cli"`** (the API's `resolve_principal`
  already honors `CHORUS_DEFAULT_IDENTITY`, so this stays consistent).
- Build `audit = AuditLogger(load_audit_env().db_path); audit.init_schema()`
  (same two lines as the API lifespan).
- Call `resolve_all(driver, load_resolution_env(), audit, user=principal)`.
- Print the summary as today.

## Testing (TDD)

- `resolve_all` writes **one** `"resolve_all"` audit row with `status="ok"`,
  `result_count == processed`, and `entities_touched` covering the minted/attached
  entity ids — asserted against the `in_memory_audit` fixture's SQLite (same query
  pattern as the agent/tool tests).
- A failing run (stub `provider.embed` to mismatch length → the `ValueError` from
  #6) writes an audit row with `status="error"` and a non-null message — no silent
  gap.
- An empty run (no unresolved aliases) writes **no** audit row (documents the
  early-return behavior).
- CLI `test_cli_resolve` extended: still prints the summary, and an audit row
  exists afterward with `user` set to the resolved principal.
- Every existing `resolve_all` call site in tests updated to pass
  `in_memory_audit` + `user=`.

## ADR 0010

Record: §76 audit logging extends from query tools to the resolution **write**
path; one row per run via the existing `AuditLogger.time_tool`; per-alias detail
stays on `RESOLVED_TO`. Note the scope nuance (compliance.md framed §76 as "query
logging"; this widens it to a write/ingestion operation) and the rejected
granularities (per-alias: millions of rows, duplicates edge provenance; per-mint:
more code, partial coverage).

## Out of scope

Auditing the ingestion `run` pass (postings/comments/messages/profiles/connections)
remains unaudited — this issue is scoped to resolution. Flag as a possible future
consistency follow-up in the ADR; not built here.
