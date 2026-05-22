# 0006 — Profiles table (author-profile enrichment)

Status: accepted
Date: 2026-05-22

## Context

ADR 0002 deferred the `connections` ingestion path until the upstream
schema arrived, on the assumption — carried from CLAUDE.md — that
`connections` is a single table of follower/following + friendship
*edges*, one relationship per row.

The upstream system in fact emits **two** distinct tables under the
"connections" umbrella:

1. A **profile-per-row** table — one row per author profile, carrying
   identity, profile metadata, sensitive personal fields, and
   denormalized relationship columns.
2. A **node-edge-node** table — one social-graph relationship per row.

The profile-per-row table's schema has been delivered; the
node-edge-node table's has not. ADR 0002, `connections.py`
(`write_follows` / `write_friendships`), `UpstreamAdapter.fetch_connections`,
and the `:FOLLOWS` / `:FRIENDS_WITH` indexes all target table 2.

## Decision

Treat the two as two distinct upstream tables with two distinct
ingestion modules.

- Table 2 (node-edge-node) remains the `connections` table and remains
  deferred — ADR 0002 still governs it.
- Table 1 (profile-per-row) is ingested by a new
  `chorus/ingestion/profiles.py` module as **`:Author` enrichment**, not
  as a social graph:
  - Each row enriches the existing `:Author` node. The join key is the
    upstream `ID` column — the network author id, equal to the
    `Author ID` carried by the postings/comments tables and used as
    `:Author.id`. The upstream `UUID` is stored as a `profile_uuid`
    property, never used as a key.
  - The write is `MERGE (a:Author {id}) SET a += $props` — `SET`, not
    the write-once `ON CREATE SET` the artifact stages use, because the
    profiles table is the authoritative source for author identity and
    profiles mutate (`Date Last Updated`). `$props` excludes columns the
    row did not supply, so a sparse crawl never wipes data.
  - The denormalized relationship/aggregate columns (`Friends`,
    `Connected Users`, `Postings`, `Comments`, `Groups`, `Media Items`,
    `Co Author of Postings`, `Quoted in Postings`, `Chat Messages`) are
    **not** mapped to graph edges — they duplicate edges the artifact
    tables (and, later, the edge table) already own. The raw store
    captures the full upstream row, so they are preserved verbatim
    without being modeled.
  - `:Author` gains properties only — no migration is required.
- Personal profile fields (`bio`, `date_of_birth`, `hometown`,
  `work_education`, `current_city`) are stored on `:Author` and are
  **retained indefinitely** — not subject to the nightly retention
  sweep, which operates on `:Post` and already skips `:Author`. This is
  a deliberate decision; see `docs/compliance.md`.

## Open questions

- `Target Profile` and `Profile Owner` columns: semantics unclear from
  the header alone. Not modeled in v1 — preserved in the raw store,
  pending clarification from the upstream provider.
- Indefinite retention of profile personal data must be confirmed by
  the DSFA. Date of birth and free-text bio are personal data and may
  touch Art. 9 categories.

## Alternatives considered

- **One combined `connections.py` for both tables.** Rejected — the two
  tables have different shapes (profile rows vs. edge rows) and
  different purposes; conflating them would be a permanent source of
  confusion. The vendor's shared "connections" label is not a reason to
  merge them in chorus.
- **A separate `:AuthorProfile` node for the personal fields, with its
  own retention timer.** Considered for retention granularity, but the
  decision to retain profile data indefinitely removes the rationale —
  there is no per-field retention to isolate. Enriching `:Author`
  directly is simpler and matches the existing pattern.
- **Build the social graph from the profile table's `Friends` /
  `Connected Users` columns.** Rejected — those columns are
  denormalized; the node-edge-node table (table 2) is the authoritative
  source for relationships.

## Consequences

- Positive: the profile table can be ingested now, independently of the
  still-pending edge table; `:Author` nodes carry real identity and
  profile data instead of the thin records the artifact stages produce.
- Negative: profile personal data has no expiry path; if the DSFA later
  requires one, a `retention_until` on `:Author` and a sweeper
  extension must be added.
- ADR 0002 is unchanged and still governs the node-edge-node edge table.
- Reversal trigger: the upstream consolidates the two tables, or the
  DSFA mandates author-data retention — either would reopen the storage
  and retention decisions above.
