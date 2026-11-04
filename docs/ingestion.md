# Ingesting data

How data gets from the upstream CSV exports into the graph. The pipeline
stages themselves (pull adapter, extraction, normalization, clustering,
LLM tie-break, graph write) are described in *Ingestion pipeline* in
[`CLAUDE.md`](../CLAUDE.md); the upstream table shapes are in *Upstream
data format* there. This page is the operator's view: what to run, in
what order, and which env vars steer it.

## Running a pass

The ingestion pipeline reads CSV dumps of the upstream tables from
`INGESTION_SOURCE_DIR`, writes them to the SQLite raw store, and projects
them into the graph:

```bash
uv run python -m chorus.ingestion.cli run             # one full pass
uv run python -m chorus.ingestion.cli run --since 2026-01-01T00:00:00
```

`--since` restricts the pull to rows newer than the cutoff. Entity
extraction runs inline per post when `NER_ENABLED=true` and a GLiNER
endpoint is configured (`NER_API_BASE`); leave it off in dev
environments without one to avoid a connect-failure warning per post.

Extraction attaches each span to an `:Alias` node. Once a pull (with NER)
has run, resolve those aliases onto canonical entities:

```bash
uv run python -m chorus.ingestion.cli resolve
```

This clusters the unresolved `:Alias` nodes onto `:Entity` nodes — vector
similarity over `Entity.embedding` plus a same-type filter and an LLM
tie-break, minting a new entity when nothing matches — and writes
`:RESOLVED_TO` provenance. It needs the inference endpoint (it embeds the
surface forms and asks the chat model to break ties) and is idempotent, so
a re-run only resolves aliases added since. Because the tools read
through `:RESOLVED_TO`, a resolve pass clusters their results by canonical
entity with no tool change. Thresholds are env-driven
(`RES_EMBED_THRESHOLD`, `RES_LLM_TIEBREAK`, `RES_VECTOR_K`).

`make ingest` and `make resolve` run the same two stages inside the
running backend container (`docker compose exec`, so the backend must be
up); use them for bulk or server-side loads. `make ingest` reads
`INGESTION_SOURCE_DIR` as the running backend sees it: the host drop dir
(`INGEST_HOST_DIR`, default `~/chorus/ingest`, mounted read-only) when the
backend was started with the dev overlay (`make dev` / `make up-dev`),
`CHORUS_HOME/ingest` on the `chorus-state` volume in the production shape.
An authenticated user can also drive upload plus migrate/ingest/resolve as
background jobs from the React SPA's ingestion screen, gated by
`INGESTION_UI_ENABLED` (default off; ADR 0014).

Every pass, resolve run and migration is recorded on the data-protection
audit trail ([audit.md](audit.md)): the make targets attribute it to the
invoking host user and take the stated purpose from `CASE_REF` / `REASON`
(required when `AUDIT_REQUIRE_PURPOSE` names the command's actions):

```bash
make ingest CASE_REF=case-0001 REASON="monthly import"
```

## Related

- [architecture.md](architecture.md) — runtime shape, the data-plane and
  inference contracts, the nginx upload limit that bounds CSV uploads.
- [retention.md](retention.md) — `retention_until`, set at ingestion, and
  the cascade rules for expiry.
- [audit.md](audit.md) — what each pass, resolve run and migration records
  on the §76 BDSG audit trail.
- [compliance.md](compliance.md) — the compliance and retention posture.
- [`docs/decisions/`](decisions/) — ADR 0006 (profiles), ADR 0007
  (connections), ADR 0011 (retention anchored on `ingested_at`),
  ADR 0012 (durable alias `norm_key`), ADR 0014 (frontend ingestion).
