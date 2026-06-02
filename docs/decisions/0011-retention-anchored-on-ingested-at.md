# 0011 — Retention anchored on a chorus-set `ingested_at`

Status: accepted
Date: 2026-06-02

## Context

Per-post retention (`Post.retention_until`, swept nightly — see
`docs/retention.md`) needs an anchor timestamp to count from. Upstream rows
carry two times — `Timestamp` (content creation) and `Crawled at` (when the
upstream crawler fetched the row) — but neither is a good retention anchor for
chorus:

- They are **optional and uneven** across artifact types. Chat `messages`
  carry no `Crawled at` column at all, and any artifact may arrive with a
  missing or blank `Timestamp`. An earlier iteration anchored
  postings/comments on `Crawled at` and messages on `Timestamp`, which forced
  a per-type special-case and still left rows with no usable anchor.
- They describe the **upstream's** timeline, not chorus's. The crawling
  software already maintains its own retention timer on its own store; chorus
  re-deriving retention from those fields couples two independent retention
  policies.

Chorus's retention question is "how long should chorus keep this after it
ingested it?" — that is about chorus's ingestion time, not the upstream's
timestamps.

## Decision

Add a chorus-set **`ingested_at`** timestamp to every artifact
(`:Post:Posting` / `:Comment` / `:Message`) and anchor retention on it:

- `ingested_at` is set by chorus at ingestion, **not** read from the upstream
  row. `run_once` computes one value per run (`datetime.now(UTC)`, injectable
  for tests) and stamps it on every artifact, so a run is internally
  consistent.
- `retention_until = RetentionConfig.until(ingested_at)` uniformly for all
  three artifact types — the messages special-case is removed.
- It is written `ON CREATE SET` only, so re-ingesting a row keeps its original
  `ingested_at` / `retention_until`: retention is measured from **first**
  ingestion.
- `Timestamp` and `Crawled at` become **optional, informational** properties.
  No upstream time field can drop a row; only genuinely malformed rows
  (missing `UUID` / `Author ID` / `Network`) are skipped by the ingestion
  skip helper. `RETENTION_ENABLED=false` still bypasses retention entirely
  (every `retention_until` is `null`).

## Consequences

- Retention is decoupled from upstream timestamps and uniform across artifact
  types; messages are no longer a special case.
- Re-crawls / re-ingests do not extend a post's retention clock (`ON CREATE`
  only).
- **Breaking for existing graph data**: nodes ingested before this change have
  no `ingested_at`. Acceptable during early development (wipe + re-ingest); a
  backfill migration would be required otherwise.
- A future retention sweeper deletes where `retention_until` is non-null and in
  the past; a `null` deadline (`RETENTION_ENABLED=false`, or pre-change nodes)
  is never swept. Consider indexing `retention_until` when the sweeper lands.

## Alternatives considered

- **Anchor on `Crawled at` (postings/comments) + `Timestamp` (messages).** The
  immediately prior iteration; rejected for the per-type special-case and for
  coupling chorus retention to upstream timestamps that may be absent.
- **Anchor on content `Timestamp`.** The original design (superseded). Dropped
  rows that had no creation time and conflated "when written" with "how long
  chorus keeps it."
