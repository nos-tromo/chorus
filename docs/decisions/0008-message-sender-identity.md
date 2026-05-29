# 0008 — Chat-message sender identity

Status: proposed
Date: 2026-05-29
Relates to: [0006](0006-profiles-table.md), [0007](0007-connections-schema.md)

## Context

`:Author` identity in chorus is keyed on the upstream **network author
id**. The postings and comments tables carry an `Author ID` column that
becomes `:Author.id`; `profiles.csv` enriches the same node by joining
on its `ID` column (the network author id) per ADR 0006; the
`connections` edge table likewise resolves endpoints by network id per
ADR 0007. One canonical key, shared across every author-bearing table.

The **messages table is the exception.** Its real upstream header is:

```
UUID;Chat ID;Sender;Timestamp;Text;Tags;URL;Chat Group;Answers Count;Reply To;Network
```

There is no numeric sender-id column and no vanity/handle column — the
only sender field is `Sender`, a free-text **display name** (observed
values: `"Gunnar Scherf"`, `"AfD Kreisverband Harburg-Land"`,
`"Delia Klages, MdL"`). `chorus/ingestion/messages.py` therefore keys
the sender `:Author` on the `Sender` string:

```cypher
MERGE (a:Author {id: $sender_id})   -- $sender_id = the Sender NAME
```

This was surfaced by an `authors_connected_by_topic` export in which
`author_id`, `handle`, and `display_name` were sparsely populated. The
investigation (2026-05-29) found two separable problems:

1. **`display_name` always null — a plain bug, fixed alongside this
   ADR.** `from_row` read a non-existent `"Sender Display Name"` column.
   It now reads `Sender`, the only human-readable identity the table
   provides. (Test:
   `test_messages_from_row_populates_display_name_from_sender`.)

2. **`:Author` keyed on a display name — the subject of this ADR.** A
   display-name key has three consequences that the `display_name` fix
   does **not** address:
   - `author_id` for a message-only author is a name, not a network id.
   - `handle` stays null: the messages table has no handle column. (The
     handle is *latent* in the message URL — `x.com/<handle>/status/…` —
     but nothing extracts it.)
   - **Identity split.** A person who both authors postings (numeric-id
     node) and sends chat messages (name node) becomes **two distinct
     `:Author` nodes**. Network and topic queries
     (`authors_connected_by_topic`, `network_around`,
     friend-of-friend) fragment and double-count them, and profile
     enrichment — which joins on the numeric `ID` — can never reach the
     name-keyed node.

The display-name key is not a coding mistake; it is the only key the
data offers. The question this ADR settles is how chorus should
*identify and unify* message senders given that constraint.

## Options considered

### A. Status quo (keep name-keyed message authors)

Leave message senders as standalone name-keyed nodes; ship only the
`display_name` fix.

- Positive: zero merge risk, no migration, message-only authors remain
  queryable by name.
- Negative: identity split persists; double-counting in network
  queries; `author_id` shows names; handle stays empty; profiles cannot
  enrich message-only authors.

### B. Re-key messages on a handle derived from the URL

Parse `(platform, handle)` out of the message URL and use it as the
message-author key (and store `handle`).

- Positive: message authors gain a real handle; a `(platform, handle)`
  key can align with the postings/comments `Vanity Name` (handle).
- Negative: URL→handle parsing is network-specific and must live behind
  the adapter boundary; not every network exposes a handle in the URL;
  the artifact tables key on the **numeric** id, not handle, so this
  *still* does not auto-merge the two nodes without a resolution step;
  changing the merge key is a migration and risks collapsing distinct
  people who share a handle string.

### C. Author identity resolution stage (recommended)

Treat author identity like entity identity. Ingestion keeps writing
thin per-source nodes (messages by name, others by network id); a
resolution pass then merges them into a canonical `:Author`, mirroring
the existing `normalize → lookup_alias → cluster → llm_tiebreak →
mint` pipeline in `chorus/ingestion/resolution.py` (today entity-only
and stubbed):

- **Deterministic first:** `(platform, handle-from-URL)` ==
  `(platform, vanity_name)` → merge with high confidence.
- **Then fuzzy:** normalized display-name + embedding cluster + LLM
  tiebreak for ambiguous cases; thresholds in `ResolutionConfig`, not
  constants (CLAUDE.md §Ingestion pipeline).
- **Reversible:** record the merge as an author-alias relationship
  (parallel to `(:Alias)-[:RESOLVED_TO]->(:Entity)`) so a bad merge can
  be undone without losing the original surface form — the same
  guarantee ADR-era entity resolution gives, and the same property the
  §76 BDSG / DSFA posture wants for anything that links a person's
  activity together.

- Positive: architecturally consistent — chorus already commits to
  resolution-as-a-stage and reversible aliases; solves the *general*
  cross-source identity problem, not just messages; auditable and
  reversible; no destructive re-key at ingestion time.
- Negative: most work; couples to the still-pending entity-resolution
  ticket; needs threshold tuning and incurs LLM-tiebreak cost; node
  merges interact with the `:Author.id` uniqueness constraint
  (migration 001) and need a defined merge primitive.

### D. Request a stable sender id from upstream

Ask the vendor to add a numeric/stable sender id (and handle) to the
messages export.

- Positive: fixes the root at the source; messages would key like every
  other table; simplest long-term.
- Negative: depends on the provider and the airgapped delivery cadence;
  may be impossible where the source network itself exposes no stable
  id; does not repair already-ingested data.

## Recommendation

Phased, because the immediate correctness fix, the cheap enrichment,
and the structural fix have very different costs and risks:

1. **Now (landed with this ADR):** populate `display_name` from
   `Sender`. Pure correctness; no identity change.
2. **Short term — Option B's *extraction*, not its re-key:** during
   ingestion, derive the network-scoped handle from the message URL
   where the URL format is known, and set `:Author.handle` +
   `platform`. Keep keying on the `Sender` name (no destructive
   re-key). Cheap, improves the export immediately, and produces the
   deterministic join key resolution will need. Handle parsing lives
   behind the adapter / a pure-string normalizer (airgap-safe — no
   network calls).
3. **Medium term — Option C:** implement author identity resolution as
   an extension of the resolution stage. This is the actual fix for the
   identity split and for `author_id` semantics.
4. **In parallel — Option D:** raise the missing stable sender id with
   the upstream provider. If it ever arrives, identity collapses to the
   network-id key and resolution keeps only its fuzzy, cross-platform
   role.

## Consequences

- Positive: a path consistent with the existing architecture (resolution
  stage, reversible aliases, config-driven thresholds); merges stay
  auditable and reversible; network queries eventually stop fragmenting
  authors.
- Negative: until step 3 lands, message-only authors remain separate
  name-keyed nodes. `authors_connected_by_topic` and `network_around`
  may double-count a person who both posts and chats, and that author's
  `author_id` reads as a display name. This limitation should be
  surfaced in any analytical output over message-derived authors, the
  way the quoting and expected-vs-collected caveats already are.
- Negative: handle extraction is network-specific; it must stay behind
  the adapter boundary (CLAUDE.md: only the adapter knows the upstream
  schema) and remain pure string parsing.
- Compliance: unifying chat activity with a profiled person is exactly
  the kind of linkage the DSFA scopes. Author resolution (step 3) must
  be reviewed against `docs/compliance.md` before it links message
  senders to enriched profiles; reversibility is a prerequisite, not a
  nicety.
- Reversal trigger: upstream adds a stable sender id (collapses to
  Option D and retires the message-specific keying); or author
  resolution proves to generate too many bad merges in practice
  (fall back to deterministic `(platform, handle)` matching only, and
  revisit thresholds).

## Open questions

- Is the URL handle reliably present and unique per network? (X: yes;
  Telegram and others: to confirm against real exports.)
- What is the single canonical author key once resolution exists — the
  numeric network id (as today, where present) with handle as the
  bridge, or `(platform, handle)`? Postings and profiles assume the
  numeric id; that argues for keeping it canonical.
- Modeling: does author resolution reuse a dedicated author-alias
  label/edge, or a canonical-pointer edge between `:Author` nodes? The
  entity `:Alias` node is entity-specific (`surface_form → :Entity`)
  and should not be overloaded. Resolve in the implementing ticket.
