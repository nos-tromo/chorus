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
and the structural fix have very different costs and risks. Steps 1–2
have landed; this ADR stays `proposed` until the step-3 decision (the
canonical key and the merge modeling) is signed off.

1. **Landed — `display_name` fix.** Populate `display_name` from
   `Sender`. Pure correctness; no identity change.
2. **Landed — handle extraction (Option B's *extraction*, not its
   re-key).** `messages.from_row` derives the handle from the message
   URL via a conservative host+path parser (`_handle_from_url`); the
   `:Author` write records it. Identity fields (`display_name`,
   `handle`) backfill `ON MATCH` so a re-ingest repairs nodes the
   pre-fix code left null, without a wipe. Keying stays on the `Sender`
   name — no destructive re-key. Verified on the real export: 510
   message rows, 115 distinct senders, **100% now carry a handle, no
   sender mapped to conflicting handles.** This is the deterministic
   join key step 3 needs. Parsing is pure string work (airgap-safe);
   non-X hosts return `None` rather than a guessed handle, and other
   platforms are added by host as their URL grammars are confirmed
   against real exports.
3. **Pending decision — Option C: author identity resolution.** The
   actual fix for the identity split and for `author_id` semantics;
   concrete shape proposed below. Gated on the open questions and a
   DSFA review (it links a person's chat activity to their profiled
   identity).
4. **In parallel — Option D: upstream ask.** Raise the missing stable
   sender id with the provider. If it ever arrives, identity collapses
   to the network-id key and resolution keeps only its fuzzy,
   cross-platform role.

### Proposed shape for step 3 (for the decision)

A non-destructive, reversible pass in `chorus/ingestion/resolution.py`,
mirroring the entity-resolution tiers:

- **Match (deterministic tier):** for each name-keyed message-sender
  `:Author`, find a posting/comment `:Author` whose
  `(platform, vanity_name)` equals the message author's
  `(platform, handle)`, compared case-insensitively (X handles are
  case-insensitive; postings set `handle = Vanity Name`, messages now
  set `handle` from the URL). Exact match — no embedding or LLM needed
  for this tier.
- **Link, don't merge (v1):** record
  `(:Author)-[:SAME_AS]->(:Author)` from the name-keyed node to the
  network-id node (the proposed canonical, consistent with profiles
  joining on the numeric `ID`). Non-destructive and trivially
  reversible — undoing a bad link deletes one edge; no `:Post` or edge
  is rewired. Network/topic queries resolve through `SAME_AS` to the
  canonical node.
- **Ambiguity:** when a handle matches zero or many candidates, fall
  back to the `normalize → cluster → llm_tiebreak` tiers already
  stubbed in `resolution.py`, thresholds in `ResolutionConfig`. When
  still unresolved, leave the node standalone — never guess.
- **Physical merge is deferred.** Collapsing the two nodes into one
  (APOC `mergeNodes` or a hand-written relationship rewire) is heavier
  and harder to reverse; defer until `SAME_AS`-aware queries prove
  insufficient.

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
- Note: handle extraction is network-specific. It lives in the
  per-table mapping (`messages.from_row` / `_handle_from_url`) — the
  existing schema-aware layer — while the file adapter keeps yielding
  rows verbatim, and it is pure string parsing (airgap-safe). New
  platforms are added by host as their URL grammars are confirmed.
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

The proposed shape for step 3 takes a position on the canonical key
(numeric network id) and the modeling (`SAME_AS` pointer, link-not-merge);
both stand as recommendations until accepted.

- Is the URL handle reliably present and unique per network? (X: yes —
  510/510 rows, 115/115 senders, no conflicts; Telegram and others: to
  confirm against real exports.)
- What is the single canonical author key once resolution exists — the
  numeric network id (as today, where present) with handle as the
  bridge, or `(platform, handle)`? Postings and profiles assume the
  numeric id; that argues for keeping it canonical.
- Modeling: does author resolution reuse a dedicated author-alias
  label/edge, or a canonical-pointer edge between `:Author` nodes? The
  entity `:Alias` node is entity-specific (`surface_form → :Entity`)
  and should not be overloaded. Resolve in the implementing ticket.
