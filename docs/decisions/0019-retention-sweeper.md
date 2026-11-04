# 0019 — Retention sweeper: off until switched on; threads and copies go together; authors stay

Status: accepted
Date: 2026-11-04
Implements: [0011](0011-retention-anchored-on-ingested-at.md) (the sweeper it anticipates)
Keeps: [0006](0006-profiles-table.md) (author data retained indefinitely)

## Context

Every post has carried a `retention_until` since the first release, and
`docs/retention.md` described a nightly sweep with a cascade (comments,
replies, attachments, orphaned aliases) that never landed: expired posts
stayed in the graph and their raw rows in the raw store indefinitely.

A post has copies beyond its node. The raw store keeps a row per ingestion,
and a comment's row repeats its posting's text, a reply's its parent's.
Extraction leaves aliases (surface forms from the text), the posting writer
attachments. Rows the ingestion skips stay in the raw store without any
post, and a staged upload whose job never ran stays under `CHORUS_HOME`.

Deletion is erasure under §76 BDSG and goes on the audit trail like every
other processing; the federation's rule is that the record exists before the
deletion takes effect, and that no deletion happens without it.

While checking what the existing settings stamp, one gap showed: a post that
a reply referenced before the post's own row arrived kept no properties, so
it had no deadline and would never expire. The writers now fill such a post
from its row (fixed with this change).

## Decision

1. **Nothing is deleted until an operator switches the sweeper on**
   (`RETENTION_SWEEP_ENABLED`, default off, also in the compose shape).
   Deadlines are still stamped as before (`RETENTION_ENABLED`,
   `RETENTION_DAYS_DEFAULT`). Before switching it on, the dry run
   (`make retention-report`) counts what a sweep would delete now and what it
   would leave, from counts and date ranges only; it records nothing. Once
   on, the backend sweeps every `RETENTION_SWEEP_INTERVAL_S` (default a day;
   `0` leaves sweeps to `make retention-sweep`). The one-off command obeys the
   switch as well, and its refusal is recorded as `denied`: a deployment that
   has not decided to delete cannot be made to by a mistyped target.
2. **A post goes with its thread and its copies.** A posting takes the
   comments on it; every post takes the replies to it and to them, whatever
   their own deadline. What only the deleted posts referenced goes with
   them: aliases, hashtags, attachments, and bare parent references (post
   nodes that hold nothing but a uuid). So do all their raw-store rows.
3. **Authors, entities, groups and platforms stay**, and so do the
   `profiles` and `connections` raw rows. ADR 0006's policy is unchanged.
   The dry run counts the authors a sweep leaves without any post, without
   any post or social edge, and with profile data among those, so the policy
   can be reviewed on numbers (see *Open questions*).
4. **Copies without a deadline of their own** — raw rows no post holds and
   stale staged uploads — go once received longer ago than the period in
   force, and only while retention is on.
5. **Recorded before it takes effect, in bounded batches.** A batch holds up
   to `RETENTION_SWEEP_BATCH_SIZE` posts and never splits a thread. Its one
   `delete` / `retention.sweep` event (authors, entities and posts by
   reference; period, cutoff and counts as criteria; continuation events
   beyond 200 ids) is written inside the graph and raw-store transactions
   before either commits; if it cannot be written both roll back. The raw
   store commits first, so a failed graph commit leaves the posts expired for
   the next sweep; a commit that fails after its event is recorded with
   `outcome="error"`. Files are recorded first, then deleted, with an `error`
   event for what is left.
6. **A failed audit write stops the sweep**, visible as
   `chorus_retention_sweep_halted` on `/metrics` and in the operational log
   (counts only). The next interval tries again; nothing commits until an
   event can be written. A lock under `CHORUS_HOME` keeps the scheduled and a
   one-off sweep apart.

## Alternatives considered

- **Let the one-off command delete without the switch.** It is an explicit
  operator action, but `retention-sweep` sits next to `retention-report`;
  one typo would delete on a deployment that never chose to. Rejected.
- **Keep later replies when their parent expires.** Their raw rows repeat
  the parent's text, and the graph would keep fragments of a deleted thread.
  Rejected; the dry run shows how many posts go early this way.
- **One event per deleted post.** Millions of events on a first sweep of an
  old store, for what one event per batch records by reference. Rejected, as
  ADR 0018 did for bulk transfers.
- **A sweeper container, or a host timer only.** A thread in the backend
  shares its audit chain, settings and volume and needs no new image. A host
  timer remains possible: interval `0`, and the timer calls
  `make retention-sweep`.
- **A transaction spanning Neo4j and SQLite.** Not available; the commit
  order and the error records above replace it.

## Consequences

- Positive: the deadlines are enforced once an operator decides so, and every
  deletion is on the trail before it happens; the dry run makes the decision
  an informed one.
- Negative: a thread goes when its first post expires, before the later
  replies' own deadlines. Entities keep the canonical names and embeddings of
  surface forms whose posts are gone. Author data stays indefinitely. The
  operator's source exports and data-plane's backups are outside the sweep.
- Reversal trigger: the DSFA requires author data to expire (open question
  1), or a thread so large that one batch holds a lock too long (split
  threads by depth, deepest replies first).

## Open questions

1. **Authors left without any post or social edge** (counted by the dry
   run). Options, from least to most change to ADR 0006:
   a. keep them, as today;
   b. delete only those without profile data — a bare id and handle carry no
      content once their posts are gone, while profile-enriched authors stay
      under ADR 0006;
   c. strip the personal profile fields (`bio`, `date_of_birth`, …) of
      isolated authors and keep the node, so the graph's history keeps its
      reference;
   d. give `:Author` a deadline of its own (renewed by each post, profile or
      connection that names the author) and sweep authors whose deadline
      passed and who have no post left, with their `profiles` and
      `connections` raw rows — the per-author timer ADR 0006 names as its
      reversal path.
2. **Entities left without an alias**: keep (today) or delete with their
   last alias.
3. **Groups left without a post**: keep (today) or delete.
4. **Source exports** under `INGESTION_SOURCE_DIR` and **backups** are not
   swept; the operator and data-plane's rotation decide when those copies go.
