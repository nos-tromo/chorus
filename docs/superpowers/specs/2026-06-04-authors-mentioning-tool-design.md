# `authors_mentioning` tool — design

**Date:** 2026-06-04
**Status:** approved (design); implemented in PR #37
**Scope:** add one graph-only retrieval tool, `authors_mentioning(entity, from, to,
limit)`, that ranks the authors who mention an entity. It is the author-valued
sibling of `posts_mentioning` and reuses that tool's matching semantics verbatim.

## Context & goal

Chorus ships four graph tools (`posts_mentioning`, `author_activity_summary`,
`topic_co_occurrence`, `authors_connected_by_topic`) plus an NL agent that
orchestrates them. None pivots **entity → authors**:

| Tool | Seeded by | Returns |
|---|---|---|
| `posts_mentioning` | entity | posts (no author field) |
| `author_activity_summary` | author | that author's top *topics* (reverse direction) |
| `authors_connected_by_topic` | **author** | other authors sharing topics |
| `topic_co_occurrence` | entity/topic | other *topics*, not authors |

So "which authors talk about entity X, ranked" — a natural enumeration query over
data already in the graph — has no tool. The 2026-05-29 graph-tools round picked
one exemplar per query-type and never reached this flavour of *Enumeration*; it was
not consciously rejected, and there is no ADR or open ticket against it. The
traversal already exists inside `authors_connected_by_topic.cypher` (the
`(:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->` leg), just never exposed
entity-first.

**Goal:** expose that leg as a dedicated, audited, single-purpose tool — keeping the
counting/ranking in version-controlled Cypher rather than pushing it onto the
agent or UI.

## Why a dedicated tool (rejected alternatives)

- **Extend `posts_mentioning` to return the author per hit, group client-side.**
  Rejected: pushes the count/rank to the caller (the LLM or the UI), so mention
  counts are non-deterministic, never land in the audit row as a clean
  `result_count`, and the call returns one row per post (potentially thousands) to
  answer a question about a handful of authors.
- **Let the NL agent compose it from `posts_mentioning`.** Rejected: unreliable at
  scale, and invisible to the structured (non-agent) UI, which is the primary
  surface for non-technical analysts.

A dedicated tool keeps aggregation in Cypher (auditable, deterministic) and serves
both surfaces. Cost is small — the established pattern carries everything else.

## Matching semantics — mirror `posts_mentioning` exactly (load-bearing)

The seed `entity` string is matched against the `MENTIONS` target with the **same
rule** `posts_mentioning.cypher` uses (lines 14–25). For a trimmed, case-folded
query `q`, a mention node `m` matches when either:

- `m` is an `:Entity` and `toLower(m.canonical_name) = q`, **or**
- `m` is an `:Alias` and (`toLower(m.surface_form) = q` **or** its resolved
  entity's `toLower(e.canonical_name) = q`, via `(m)-[:RESOLVED_TO]->(e)`).

This yields the **lockstep guarantee**:

> For posts with a non-null timestamp **and body text** (the universe
> `posts_mentioning` operates on — it filters `text/timestamp IS NOT NULL`), the
> authors returned by `authors_mentioning(X)` are exactly the distinct authors of
> the posts returned by `posts_mentioning(X)`.

That makes the tool trivially explainable ("the authors behind the posts
`posts_mentioning` would return") and gives us a cross-tool regression test (below).
The alternative — `topic_co_occurrence`-style seed expansion, which spans *all
sibling aliases* of a resolved entity when seeded by one surface form — was
**considered and rejected** for this tool: it would make `authors_mentioning(X)`
and `posts_mentioning(X)` diverge for the same input. If entity-spanning is ever
wanted, it is the same change applied to **both** enumeration tools together so
they never drift.

`MENTIONS` terminates at `:Alias`; resolution (shipped — `resolution.py`,
migration `004_alias_norm_key`) adds `(:Alias)-[:RESOLVED_TO]->(:Entity)`. The
`OPTIONAL MATCH … RESOLVED_TO` + the rule above already cover both the resolved and
unresolved states, exactly as `posts_mentioning` does — no behavioural toggle.

## What carries over for free (no changes needed)

- **Dispatch is registry-driven.** `api/routers/tools.py` builds `GET /tools` and
  `POST /tools/{name}` by iterating `TOOLS.values()` / `TOOLS.get(name)`; the NL
  agent builds its OpenAI tool list from the same registry
  (`agent/openai_tools.py`). Registering the tool surfaces it to **both** the REST
  surface and the agent automatically. No router, client, or agent edits.
- **Registration** is a one-line import in `chorus/tools/__init__.py` (the
  `@register_tool`/`@audited` decorators self-register on import).
- **Audit** — `@audited` writes exactly one row per call from
  `audit_entities()` + `audit_result_count()` on the output model.

## Tool spec

One Cypher template in `queries/authors_mentioning.cypher` (never inline) + one
module in `tools/authors_mentioning.py` (`@register_tool` + `@audited` + Pydantic
in/out) + one Streamlit page `ui/pages/05_authors_mentioning.py`. The Cypher below
is illustrative; exact Cypher is written during implementation.

### Input — `AuthorsMentioningIn`

- `entity: str` — entity canonical name or alias surface form, case-insensitive
  (matched per *Matching semantics* above).
- `from_: datetime | None` (alias `from`) — inclusive lower bound on
  `Post.timestamp`.
- `to: datetime | None` — **exclusive** upper bound. Window is half-open
  `[from, to)`, matching `author_activity_summary` / `topic_co_occurrence`.
- `limit: int = 50` (1..500) — max authors returned.

`model_config = {"populate_by_name": True}` so `from`/`from_` both work, identical
to the sibling tools.

### Output — `AuthorsMentioningOut`

`authors: list[AuthorMention]`, ranked by `mention_post_count` desc, tiebreak
`author_id` asc (deterministic). Empty list when the seed matches nothing.

`AuthorMention`:

- `author_id: str`, `handle: str | None`, `display_name: str | None`,
  `platform: str | None` — identity, so distinct same-named authors are
  distinguishable and never merged.
- `mention_post_count: int` — count of **distinct** posts authored by this author
  that mention the entity (a post naming the entity twice counts once).
- `first_mention: datetime | None`, `last_mention: datetime | None` — min/max
  timestamp over those posts (null only when all matching posts lack a timestamp).

Audit hooks:

- `audit_entities()` → distinct resolved entity ids the seed matched (the
  `:Entity.id`, or the resolved `e.id` for an alias hit), nulls dropped. Empty for
  an unresolved alias-only seed — correct and honest, exactly like
  `posts_mentioning`.
- `audit_result_count()` → `len(authors)`.

### Traversal sketch

```cypher
MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(mention)
OPTIONAL MATCH (mention:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH a, p, mention, e, labels(mention) AS mention_labels, trim($entity) AS q
WHERE (
        ("Entity" IN mention_labels AND toLower(coalesce(mention.canonical_name,"")) = toLower(q))
     OR ("Alias"  IN mention_labels AND (
            toLower(coalesce(mention.surface_form,"")) = toLower(q)
         OR toLower(coalesce(e.canonical_name,""))     = toLower(q)))
      )
  AND ($from IS NULL OR p.timestamp >= datetime($from))
  AND ($to   IS NULL OR p.timestamp <  datetime($to))
WITH a,
     count(DISTINCT p)                                   AS mention_post_count,
     min(p.timestamp)                                    AS first_mention,
     max(p.timestamp)                                    AS last_mention,
     collect(DISTINCT CASE WHEN mention:Entity THEN mention.id ELSE e.id END) AS entity_ids
RETURN a.id AS author_id, a.handle AS handle, a.display_name AS display_name,
       a.platform AS platform, mention_post_count, first_mention, last_mention
ORDER BY mention_post_count DESC, author_id ASC
LIMIT $limit;
```

`entity_ids` (nulls dropped) feeds `audit_entities()`. Reuse `posts_mentioning`'s
`AND`/`OR` parenthesisation precisely — that precedence bug (an unparenthesised
`OR` silently disabling the time filter) is the exact class the time-window
regression test guards against.

### No text/timestamp-not-null filter (deliberate divergence)

`posts_mentioning` requires `p.text IS NOT NULL AND p.timestamp IS NOT NULL`
because it returns body text and orders by time. `authors_mentioning` returns
neither, so it imposes **no** such filter: a mention on a timestamp-less post is a
real mention and counts toward `mention_post_count` (timestamps are "optional and
informational" per CLAUDE.md; ingestion never drops a row for a missing one). The
lockstep guarantee is therefore scoped to timestamped posts — the normal case, and
the case the cross-tool test fixture uses. Mentions only attach to text-bearing
posts (NER runs on text), so the absence of a text filter changes nothing in
practice.

## Agent disambiguation

The first docstring line becomes the OpenAI tool description (enforced by
`tests/tools/test_registry.py`). It must steer the model away from the three
cousins:

> `Rank the authors who mention an entity, by how many of their posts mention it,
> within an optional time range.`

(vs. `posts_mentioning` = the *posts*; `author_activity_summary` = needs a known
*author*; `authors_connected_by_topic` = needs a seed *author*.)

## Testing

New file `tests/integration/test_authors_mentioning.py` (per-tool tests live in
`tests/integration/`, not `tests/tools/`), against an ephemeral Neo4j over a small
fixture. Assert:

- **Ranking & counting** — authors ordered by `mention_post_count` desc; a post
  mentioning the entity twice counts once (`count(DISTINCT p)`); `limit` caps the
  list.
- **Time-window correctness** — a dedicated `[from, to)` regression test (the
  `posts_mentioning` precedence-bug class; every tool gets one).
- **Resolved + unresolved both match** — a fixture with both an unresolved `:Alias`
  and an `:Alias`→`:Entity` chain; seeding by canonical name and by surface form
  both return the expected authors; `audit_entities()` carries the resolved id and
  is empty for the alias-only seed.
- **Cross-tool lockstep** — over a timestamped fixture with no limit pressure,
  `{a.author_id}` from `authors_mentioning(X)` equals the distinct authors of the
  posts from `posts_mentioning(X)`. This pins the mirror guarantee.
- **No-merge** — two distinct authors with the same display name are returned as
  two rows.
- **Audit** — `audit_result_count()` and `audit_entities()` populate the row.

The inference provider is untouched (pure graph read) — no inference stubbing.

## Build sequence (TDD)

1. `tests/integration/test_authors_mentioning.py` — red first.
2. `chorus/queries/authors_mentioning.cypher`.
3. `chorus/tools/authors_mentioning.py` (`@register_tool` + `@audited` + Pydantic).
4. `chorus/tools/__init__.py` — add the import line (self-registers → REST + agent).
5. `chorus/ui/pages/05_authors_mentioning.py` — thin form over
   `ChorusClient.call_tool`, mirroring `01_posts_mentioning.py`. Caption reflects
   live data state (resolution has shipped — no "pending resolution" note).

No router/client/agent edits.

## Resolved decisions (defaults baked in)

- **Matching:** mirror `posts_mentioning` verbatim (lockstep guarantee). Not
  entity-spanning.
- **Count unit:** distinct posts per author; ranked desc, tiebreak `author_id` asc.
- **Post scope:** counts span every `:Post` the author authored — postings,
  comments, and messages — mirroring `posts_mentioning`'s `:Post` match (a mention
  on a comment the author wrote counts). Use the broad `:Post` label, not
  `:Posting`.
- **Window:** half-open `[from, to)`.
- **Inclusivity:** no text/timestamp-not-null filter; null-timestamp mentions count.
- **Surface shape:** thin leaderboard (identity + count + first/last mention).
  Engagement deltas and evidence snippets were explicitly deferred (below).
- **Spec location:** `docs/superpowers/specs/` (brainstorming default), separate
  from `docs/decisions/` ADRs. No ADR warranted — this adds a tool within an
  existing, ADR-backed pattern; it reverses no load-bearing choice.

## Deferred (with trigger)

- **Engagement deltas per author** (expected/collected reactions & comments over
  their mentioning postings). Trigger: a concrete "who drives *engaged* discussion
  of X" ask. Non-breaking additive fields; overlaps `author_activity_summary`.
- **Evidence snippets** (sample post uuids/text per author). Trigger: analysts
  asking to see *what* was said without a second `posts_mentioning` call.
- **Entity-spanning matching.** Trigger: a decision to make both enumeration tools
  span sibling aliases — changed together, not here.
