# authors_mentioning Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a graph-only retrieval tool `authors_mentioning(entity, from, to, limit)` that ranks the authors who mention an entity, the author-valued sibling of `posts_mentioning`.

**Architecture:** One Cypher template + one Pydantic-in/`@audited`-out tool module (mirroring `chorus/tools/posts_mentioning.py`), self-registered via `chorus/tools/__init__.py`. The MENTIONS-target match is copied verbatim from `posts_mentioning.cypher` so `authors_mentioning(X)` returns exactly the authors behind `posts_mentioning(X)`'s posts (the "lockstep guarantee"). Both the REST surface (`api/routers/tools.py`) and the NL agent (`agent/openai_tools.py`) build themselves from the `TOOLS` registry, so registration alone surfaces the tool to both — no router/client/agent edits. A thin Streamlit page exposes it in the structured UI.

**Tech Stack:** Python 3.12, Neo4j (Cypher), Pydantic v2, FastAPI, Streamlit, pytest + testcontainers (Neo4j 5.26.26).

**Spec:** `docs/superpowers/specs/2026-06-04-authors-mentioning-tool-design.md`

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `chorus/queries/authors_mentioning.cypher` | Create | The query: match mentions like `posts_mentioning`, aggregate per author. |
| `chorus/tools/authors_mentioning.py` | Create | Pydantic I/O models + `@register_tool`/`@audited` wrapper. |
| `chorus/tools/__init__.py` | Modify | Add one import line so the tool self-registers. |
| `tests/conftest.py` | Modify | Add the module to `_CHORUS_ENV_MODULES` so it survives the per-test reload. |
| `chorus/ui/pages/05_authors_mentioning.py` | Create | Thin Streamlit form over `ChorusClient.call_tool`. |
| `tests/integration/test_authors_mentioning.py` | Create | Integration tests against an ephemeral Neo4j. |

> **All tests below are integration tests**: each boots a Neo4j 5.26.26 testcontainer, so **Docker must be running**. Run a single test with `uv run pytest tests/integration/test_authors_mentioning.py::<name> -v`.

---

### Task 1: Failing tests — empty DB + ranking

**Files:**
- Test: `tests/integration/test_authors_mentioning.py` (Create)

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_authors_mentioning.py`:

```python
"""authors_mentioning tool: ranks authors who mention an entity."""

from __future__ import annotations

from typing import Any

from neo4j import Driver


def test_authors_mentioning_empty(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Empty database returns no authors and zero audit result count."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="Berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )
    assert out.authors == []
    assert out.audit_result_count() == 0


def test_authors_mentioning_ranks_by_post_count(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """Authors are ranked by how many of their posts mention the entity."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (al2:Alias {surface_form: 'Munich'})
            MERGE (a:Author {id: 'auth-a'})
              ON CREATE SET a.handle = 'a', a.display_name = 'Anna', a.platform = 'x'
            MERGE (b:Author {id: 'auth-b'})
              ON CREATE SET b.handle = 'b', b.display_name = 'Bob', b.platform = 'x'
            MERGE (c:Author {id: 'auth-c'}) ON CREATE SET c.handle = 'c'
            MERGE (pa1:Post:Posting {uuid: 'pa1'})
              ON CREATE SET pa1.text = 'berlin one', pa1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (pa2:Post:Posting {uuid: 'pa2'})
              ON CREATE SET pa2.text = 'berlin two', pa2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (pb1:Post:Posting {uuid: 'pb1'})
              ON CREATE SET pb1.text = 'berlin three', pb1.timestamp = datetime('2026-05-03T10:00:00+00:00')
            MERGE (pc1:Post:Posting {uuid: 'pc1'})
              ON CREATE SET pc1.text = 'munich', pc1.timestamp = datetime('2026-05-04T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(pa1)
            MERGE (a)-[:AUTHORED]->(pa2)
            MERGE (b)-[:AUTHORED]->(pb1)
            MERGE (c)-[:AUTHORED]->(pc1)
            MERGE (pa1)-[:MENTIONS]->(al)
            MERGE (pa2)-[:MENTIONS]->(al)
            MERGE (pb1)-[:MENTIONS]->(al)
            MERGE (pc1)-[:MENTIONS]->(al2)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [
        ("auth-a", 2),
        ("auth-b", 1),
    ]
    assert out.authors[0].display_name == "Anna"
    assert out.authors[0].first_mention.isoformat() == "2026-05-01T10:00:00+00:00"
    assert out.authors[0].last_mention.isoformat() == "2026-05-02T10:00:00+00:00"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_authors_mentioning.py -v`
Expected: both ERROR/FAIL with `ModuleNotFoundError: No module named 'chorus.tools.authors_mentioning'`.

---

### Task 2: Cypher template

**Files:**
- Create: `chorus/queries/authors_mentioning.cypher`

- [ ] **Step 1: Write the query**

Create `chorus/queries/authors_mentioning.cypher`:

```cypher
// authors_mentioning — authors ranked by how many of their posts mention the query
// entity, within an optional [from, to) window.
//
// The MENTIONS-target match mirrors posts_mentioning.cypher verbatim (an :Entity by
// canonical_name, or an :Alias by surface_form or its resolved entity's
// canonical_name, all case-insensitive) so authors_mentioning(X) returns precisely
// the authors behind the posts posts_mentioning(X) returns. Unlike posts_mentioning
// there is no `text/timestamp IS NOT NULL` filter: this tool returns neither body
// text nor a time ordering, and a mention on a timestamp-less post is still a real
// mention. count(DISTINCT p) collapses a post matched via several aliases/entities
// (or an alias with several :RESOLVED_TO edges) to a single contribution. Counts
// span every :Post the author authored — postings, comments, and messages.

MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(mention)
OPTIONAL MATCH (mention:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH a, p, mention, e, labels(mention) AS mention_labels, trim($entity) AS entity_query
WHERE (
    (
        "Entity" IN mention_labels
        AND toLower(coalesce(mention.canonical_name, "")) = toLower(entity_query)
    ) OR (
        "Alias" IN mention_labels
        AND (
            toLower(coalesce(mention.surface_form, "")) = toLower(entity_query)
            OR toLower(coalesce(e.canonical_name, "")) = toLower(entity_query)
        )
    )
)
  AND ($from IS NULL OR p.timestamp >= datetime($from))
  AND ($to   IS NULL OR p.timestamp <  datetime($to))
WITH a,
  count(DISTINCT p)                                                                    AS mention_post_count,
  min(p.timestamp)                                                                     AS first_mention,
  max(p.timestamp)                                                                     AS last_mention,
  collect(DISTINCT CASE WHEN "Entity" IN mention_labels THEN mention.id ELSE e.id END) AS raw_entity_ids
RETURN
  a.id           AS author_id,
  a.handle       AS handle,
  a.display_name AS display_name,
  a.platform     AS platform,
  mention_post_count,
  first_mention,
  last_mention,
  [x IN raw_entity_ids WHERE x IS NOT NULL] AS entity_ids
ORDER BY mention_post_count DESC, author_id ASC
LIMIT $limit;
```

No test run for this step (the loader is exercised once the tool module exists in Task 3).

---

### Task 3: Tool module + registration, make Task 1 green

**Files:**
- Create: `chorus/tools/authors_mentioning.py`
- Modify: `chorus/tools/__init__.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the tool module**

Create `chorus/tools/authors_mentioning.py`:

```python
"""Tool: authors_mentioning(entity, from, to, limit).

Ranks the authors who mention an entity by how many of their posts mention it,
within an optional ``[from, to)`` time window. The author-valued sibling of
``posts_mentioning``: the MENTIONS-target match is identical, so
``authors_mentioning(X)`` returns precisely the authors behind the posts
``posts_mentioning(X)`` returns (for timestamped posts).

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.posts_mentioning`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from neo4j import Driver
from pydantic import BaseModel, Field, PrivateAttr

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


def _native(value: Any) -> datetime | None:
    """Convert a Neo4j temporal value to a native ``datetime`` (or ``None``).

    Args:
        value: A Neo4j ``DateTime``, a native ``datetime``, or ``None``.

    Returns:
        ``None`` when ``value`` is ``None``; otherwise the native
        ``datetime`` (via ``to_native()`` when available).
    """
    if value is None:
        return None
    native = value.to_native() if hasattr(value, "to_native") else value
    return cast(datetime, native)


class AuthorsMentioningIn(BaseModel):
    """Input parameters for the ``authors_mentioning`` tool.

    Attributes:
        entity: Entity canonical name or unresolved alias surface form to
            filter by. Matching is case-insensitive and identical to
            ``posts_mentioning``.
        from_: Inclusive lower bound on post timestamp. ``None`` means no
            lower bound. Exposed as ``from`` on the wire.
        to: Exclusive upper bound on post timestamp. ``None`` means no upper
            bound.
        limit: Maximum number of authors to return, in [1, 500].
    """

    entity: str
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}


class AuthorMention(BaseModel):
    """One author and how many of their posts mention the entity.

    Attributes:
        author_id: Canonical ``:Author.id``.
        handle: Author handle, if known.
        display_name: Author display name, if known.
        platform: Source platform, if known.
        mention_post_count: Number of distinct posts authored by this author
            that mention the entity.
        first_mention: Earliest mentioning-post timestamp, or ``None`` when
            none of the matching posts carry a timestamp.
        last_mention: Latest mentioning-post timestamp, or ``None``.
    """

    author_id: str
    handle: str | None
    display_name: str | None
    platform: str | None
    mention_post_count: int
    first_mention: datetime | None
    last_mention: datetime | None


class AuthorsMentioningOut(BaseModel):
    """Output of the ``authors_mentioning`` tool.

    Attributes:
        authors: Matching authors, ranked by ``mention_post_count``
            descending (ties broken by ``author_id``). Empty when the seed
            matches nothing.
    """

    authors: list[AuthorMention]
    # Resolved entity ids the seed matched, stashed for the §76 audit trail.
    # Private so the public/JSON shape stays ``{"authors": [...]}``.
    _entity_ids: list[str] = PrivateAttr(default_factory=list)

    def audit_entities(self) -> list[str]:
        """Return distinct resolved entity ids the seed matched.

        Returns:
            Entity ids in first-seen order, deduped. Empty for an
            unresolved alias-only seed (no canonical id yet), mirroring
            ``posts_mentioning``.
        """
        seen: list[str] = []
        for eid in self._entity_ids:
            if eid and eid not in seen:
                seen.append(eid)
        return seen

    def audit_result_count(self) -> int:
        """Return the number of authors in this result set.

        Returns:
            Length of :attr:`authors`.
        """
        return len(self.authors)


@register_tool(
    name="authors_mentioning",
    input_model=AuthorsMentioningIn,
    output_model=AuthorsMentioningOut,
)
@audited
def authors_mentioning(
    driver: Driver,
    params: AuthorsMentioningIn,
    *,
    user: str,
    audit: AuditLogger,
) -> AuthorsMentioningOut:
    """Rank the authors who mention an entity, by how many of their posts mention it, within an optional time range.

    Loads the ``authors_mentioning.cypher`` template and runs it. The
    ``@audited`` decorator records the invocation; this function only owns
    query execution and result shaping.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        An :class:`AuthorsMentioningOut` ranked by mention-post count.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("authors_mentioning")
    cypher_params: dict[str, Any] = {
        "entity": params.entity,
        "from": params.from_.isoformat() if params.from_ else None,
        "to": params.to.isoformat() if params.to else None,
        "limit": params.limit,
    }
    authors: list[AuthorMention] = []
    entity_ids: list[str] = []
    with driver.session() as session:
        result = session.run(cypher, **cypher_params)
        for row in result:
            authors.append(
                AuthorMention(
                    author_id=row["author_id"],
                    handle=row["handle"],
                    display_name=row["display_name"],
                    platform=row["platform"],
                    mention_post_count=row["mention_post_count"],
                    first_mention=_native(row["first_mention"]),
                    last_mention=_native(row["last_mention"]),
                )
            )
            for eid in row["entity_ids"]:
                if eid and eid not in entity_ids:
                    entity_ids.append(eid)
    out = AuthorsMentioningOut(authors=authors)
    out._entity_ids = entity_ids
    return out
```

- [ ] **Step 2: Register the tool**

Modify `chorus/tools/__init__.py` — add `authors_mentioning` to the import tuple (alphabetical, after `authors_connected_by_topic`):

```python
# Self-register tools by importing their modules.
from chorus.tools import (
    author_activity_summary,  # noqa: F401
    authors_connected_by_topic,  # noqa: F401
    authors_mentioning,  # noqa: F401
    posts_mentioning,  # noqa: F401
    topic_co_occurrence,  # noqa: F401
)
```

- [ ] **Step 3: Keep the tool registered across the test reload**

Modify `tests/conftest.py` — add the module to `_CHORUS_ENV_MODULES`, right after `"chorus.tools.authors_connected_by_topic",`:

```python
    "chorus.tools.posts_mentioning",
    "chorus.tools.author_activity_summary",
    "chorus.tools.topic_co_occurrence",
    "chorus.tools.authors_connected_by_topic",
    "chorus.tools.authors_mentioning",
    "chorus.tools",
```

Rationale: `_reload_chorus()` evicts each listed module so a fresh `TOOLS` registry is rebuilt per env override. If the submodule is omitted, re-importing `chorus.tools` binds the already-loaded module without re-running `@register_tool`, so the tool would silently vanish from the registry.

- [ ] **Step 4: Run Task 1 tests to verify they pass**

Run: `uv run pytest tests/integration/test_authors_mentioning.py -v`
Expected: `test_authors_mentioning_empty` and `test_authors_mentioning_ranks_by_post_count` both PASS.

- [ ] **Step 5: Commit**

```bash
git add chorus/queries/authors_mentioning.cypher chorus/tools/authors_mentioning.py chorus/tools/__init__.py tests/conftest.py tests/integration/test_authors_mentioning.py
git commit -m "feat(tools): authors_mentioning — rank authors who mention an entity

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Guard — a post matched via two aliases counts once

**Files:**
- Modify: `tests/integration/test_authors_mentioning.py`

- [ ] **Step 1: Add the test**

Append to `tests/integration/test_authors_mentioning.py`:

```python
def test_authors_mentioning_counts_distinct_posts(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """A post that matches the query via two aliases counts once, not twice."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (a1:Alias {surface_form: 'Berlin'})
            MERGE (a2:Alias {surface_form: 'BER'})
            MERGE (a2)-[:RESOLVED_TO]->(e)
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-multi'})
              ON CREATE SET p.text = 'Berlin a.k.a. BER',
                            p.timestamp = datetime('2026-05-05T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(a1)
            MERGE (p)-[:MENTIONS]->(a2)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [("auth-a", 1)]
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/integration/test_authors_mentioning.py::test_authors_mentioning_counts_distinct_posts -v`
Expected: PASS (guards the `count(DISTINCT p)` behavior from Task 2).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_authors_mentioning.py
git commit -m "test(tools): authors_mentioning counts a post once across multiple aliases

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Regression — time window excludes entity-branch matches

**Files:**
- Modify: `tests/integration/test_authors_mentioning.py`

- [ ] **Step 1: Add the test**

Append to `tests/integration/test_authors_mentioning.py`:

```python
def test_authors_mentioning_time_window_excludes_entity_branch(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """Entity-branch mentions outside the [from, to) window exclude the author.

    Regression guard for the AND/OR precedence bug that bit posts_mentioning:
    an unparenthesised OR let the time predicates apply only to the Alias branch.
    """
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-old'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-old'})
              ON CREATE SET p.text = 'old entity-branch mention',
                            p.timestamp = datetime('2026-01-01T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(e)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn.model_validate(
            {
                "entity": "berlin",
                "from": "2026-06-01T00:00:00+00:00",
                "to": "2026-07-01T00:00:00+00:00",
                "limit": 10,
            }
        ),
        user="test-user",
        audit=in_memory_audit,
    )

    assert out.authors == []
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/integration/test_authors_mentioning.py::test_authors_mentioning_time_window_excludes_entity_branch -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_authors_mentioning.py
git commit -m "test(tools): authors_mentioning respects the [from, to) window on the entity branch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Matching parity — resolved and unresolved aliases + audit ids

**Files:**
- Modify: `tests/integration/test_authors_mentioning.py`

- [ ] **Step 1: Add the two tests**

Append to `tests/integration/test_authors_mentioning.py`:

```python
def test_authors_mentioning_resolved_alias_by_canonical_name(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """A canonical-name query matches through Alias -> Entity and records the id."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'BER'})
            MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-resolved'})
              ON CREATE SET p.text = 'resolved alias mention',
                            p.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(al)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [("auth-a", 1)]
    assert out.audit_entities() == ["ent-berlin"]


def test_authors_mentioning_unresolved_alias(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """An unresolved alias still matches the author; no entity id is recorded."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (a:Author {id: 'auth-a'}) ON CREATE SET a.handle = 'a'
            MERGE (p:Post:Posting {uuid: 'p-alias'})
              ON CREATE SET p.text = 'alias only mention',
                            p.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a)-[:AUTHORED]->(p)
            MERGE (p)-[:MENTIONS]->(al)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert [(a.author_id, a.mention_post_count) for a in out.authors] == [("auth-a", 1)]
    assert out.audit_entities() == []
```

- [ ] **Step 2: Run them to verify they pass**

Run: `uv run pytest tests/integration/test_authors_mentioning.py -k "resolved_alias_by_canonical_name or unresolved_alias" -v`
Expected: both PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_authors_mentioning.py
git commit -m "test(tools): authors_mentioning matches resolved + unresolved aliases, records entity ids

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Lockstep with posts_mentioning

**Files:**
- Modify: `tests/integration/test_authors_mentioning.py`

- [ ] **Step 1: Add the cross-tool test**

Append to `tests/integration/test_authors_mentioning.py`:

```python
def test_authors_mentioning_lockstep_with_posts_mentioning(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """authors_mentioning(X) returns exactly the authors behind posts_mentioning(X).

    Covers the matching-mirror guarantee and that comments (not just postings)
    count toward authorship.
    """
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )
    from chorus.tools.posts_mentioning import (
        PostsMentioningIn,
        posts_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (e:Entity {id: 'ent-berlin'}) ON CREATE SET e.canonical_name = 'Berlin'
            MERGE (al:Alias {surface_form: 'BER'})
            MERGE (al)-[:RESOLVED_TO]->(e)
            MERGE (al2:Alias {surface_form: 'Berlin'})
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.handle = 'a1'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.handle = 'a2'
            MERGE (p1:Post:Posting {uuid: 'p1'})
              ON CREATE SET p1.text = 'via entity', p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p2'})
              ON CREATE SET p2.text = 'via resolved alias', p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (p3:Post:Comment {uuid: 'p3'})
              ON CREATE SET p3.text = 'via unresolved alias', p3.timestamp = datetime('2026-05-03T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a1)-[:AUTHORED]->(p2)
            MERGE (a2)-[:AUTHORED]->(p3)
            MERGE (p1)-[:MENTIONS]->(e)
            MERGE (p2)-[:MENTIONS]->(al)
            MERGE (p3)-[:MENTIONS]->(al2)
            """
        )

    pm = posts_mentioning(
        migrated_driver,
        PostsMentioningIn(entity="berlin", limit=500),
        user="test-user",
        audit=in_memory_audit,
    )
    pm_uuids = [h.uuid for h in pm.hits]

    with migrated_driver.session() as s:
        rec = s.run(
            """
            MATCH (au:Author)-[:AUTHORED]->(p:Post)
            WHERE p.uuid IN $uuids
            RETURN collect(DISTINCT au.id) AS ids
            """,
            uuids=pm_uuids,
        ).single()
    expected_author_ids = set(rec["ids"])

    am = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=500),
        user="test-user",
        audit=in_memory_audit,
    )
    am_author_ids = {a.author_id for a in am.authors}

    assert am_author_ids == expected_author_ids
    assert am_author_ids == {"a1", "a2"}  # non-empty, both surfaces and a comment
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/integration/test_authors_mentioning.py::test_authors_mentioning_lockstep_with_posts_mentioning -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_authors_mentioning.py
git commit -m "test(tools): authors_mentioning is in lockstep with posts_mentioning

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Distinct same-named authors are not merged

**Files:**
- Modify: `tests/integration/test_authors_mentioning.py`

- [ ] **Step 1: Add the test**

Append to `tests/integration/test_authors_mentioning.py`:

```python
def test_authors_mentioning_does_not_merge_same_display_name(
    migrated_driver: Driver, in_memory_audit: Any
) -> None:
    """Two distinct authors sharing a display name are returned as two rows."""
    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (a1:Author {id: 'a1'}) ON CREATE SET a1.display_name = 'Alex', a1.handle = 'alex1'
            MERGE (a2:Author {id: 'a2'}) ON CREATE SET a2.display_name = 'Alex', a2.handle = 'alex2'
            MERGE (p1:Post:Posting {uuid: 'p1'})
              ON CREATE SET p1.text = 'b1', p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p2'})
              ON CREATE SET p2.text = 'b2', p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a2)-[:AUTHORED]->(p2)
            MERGE (p1)-[:MENTIONS]->(al)
            MERGE (p2)-[:MENTIONS]->(al)
            """
        )

    out = authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="berlin", limit=10),
        user="test-user",
        audit=in_memory_audit,
    )

    assert len(out.authors) == 2
    assert {a.author_id for a in out.authors} == {"a1", "a2"}
    assert all(a.display_name == "Alex" for a in out.authors)
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/integration/test_authors_mentioning.py::test_authors_mentioning_does_not_merge_same_display_name -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_authors_mentioning.py
git commit -m "test(tools): authors_mentioning keeps distinct same-named authors separate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Audit row written

**Files:**
- Modify: `tests/integration/test_authors_mentioning.py`

- [ ] **Step 1: Add the test**

Append to `tests/integration/test_authors_mentioning.py`:

```python
def test_audit_row_written(migrated_driver: Driver, in_memory_audit: Any) -> None:
    """One audit row is written per tool call, with the resolved user."""
    import sqlite3

    from chorus.tools.authors_mentioning import (
        AuthorsMentioningIn,
        authors_mentioning,
    )

    authors_mentioning(
        migrated_driver,
        AuthorsMentioningIn(entity="Nowhere"),
        user="alice",
        audit=in_memory_audit,
    )
    rows = (
        sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT user, tool_name, result_count, status FROM audit_log")
        .fetchall()
    )
    assert rows == [("alice", "authors_mentioning", 0, "ok")]
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/integration/test_authors_mentioning.py::test_audit_row_written -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_authors_mentioning.py
git commit -m "test(tools): authors_mentioning writes exactly one audit row per call

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Streamlit UI page

**Files:**
- Create: `chorus/ui/pages/05_authors_mentioning.py`

- [ ] **Step 1: Write the page**

Create `chorus/ui/pages/05_authors_mentioning.py` (mirrors `01_posts_mentioning.py`):

```python
"""UI for the `authors_mentioning` tool."""

from __future__ import annotations

import os

import streamlit as st

from chorus.ui.client import ChorusClient

st.set_page_config(page_title="authors_mentioning — chorus")


@st.cache_resource
def _client() -> ChorusClient:
    """Construct (or return the cached) :class:`ChorusClient` for this page.

    Returns:
        A :class:`ChorusClient` bound to the configured API URL and
        identity, both pulled from the environment with development
        defaults (``http://localhost:8000`` and ``"dev"``).
    """
    return ChorusClient(
        base_url=os.environ.get("CHORUS_API_URL", "http://localhost:8000"),
        identity=os.environ.get("CHORUS_UI_IDENTITY", "dev"),
    )


client = _client()

st.title("authors mentioning an entity")
st.caption("Authors ranked by how many of their posts mention the entity.")

entity = st.text_input("Entity name or alias", value="")
limit = st.slider("Limit", min_value=1, max_value=200, value=50)

col_from, col_to = st.columns(2)
from_dt = col_from.text_input("From (ISO timestamp, optional)", value="")
to_dt = col_to.text_input("To (ISO timestamp, optional)", value="")

if st.button("Search", disabled=not entity):
    payload: dict[str, object] = {"entity": entity, "limit": limit}
    if from_dt:
        payload["from"] = from_dt
    if to_dt:
        payload["to"] = to_dt
    try:
        result = client.call_tool("authors_mentioning", payload)
    except Exception as exc:
        st.error(f"tool call failed: {exc}")
    else:
        authors = result.get("authors", [])
        st.write(f"{len(authors)} author(s)")
        if authors:
            st.dataframe(authors, use_container_width=True)
        else:
            st.info("no authors")
```

- [ ] **Step 2: Smoke-check the import**

Run: `uv run python -c "import ast; ast.parse(open('chorus/ui/pages/05_authors_mentioning.py').read()); print('ok')"`
Expected: `ok` (the page is exercised by Streamlit at runtime; no unit test).

- [ ] **Step 3: Commit**

```bash
git add chorus/ui/pages/05_authors_mentioning.py
git commit -m "feat(ui): authors_mentioning page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Lint**

Run: `uv run ruff check chorus/tools/authors_mentioning.py chorus/ui/pages/05_authors_mentioning.py tests/integration/test_authors_mentioning.py tests/conftest.py chorus/tools/__init__.py`
Expected: `All checks passed!`

- [ ] **Step 2: Format**

Run: `uv run ruff format chorus/tools/authors_mentioning.py chorus/ui/pages/05_authors_mentioning.py tests/integration/test_authors_mentioning.py`
Expected: `3 files left unchanged` (or files reformatted — if so, re-stage them).

- [ ] **Step 3: Type-check**

Run: `uv run mypy chorus/tools/authors_mentioning.py chorus/ui/pages/05_authors_mentioning.py`
Expected: `Success: no issues found`.

- [ ] **Step 4: Full test suite**

Run: `uv run pytest tests/integration/test_authors_mentioning.py tests/tools/test_registry.py -v`
Expected: all `test_authors_mentioning.py` tests PASS; `test_all_tools_have_descriptions` PASS (it now also validates the new tool's description).

- [ ] **Step 5: Pre-commit on the whole change**

Run: `uv run pre-commit run --all-files`
Expected: ruff + mypy hooks pass. If a hook reformats anything, re-stage and re-run.

- [ ] **Step 6: Commit any formatting fixups**

```bash
git add -A
git commit -m "chore(tools): formatting/lint fixups for authors_mentioning

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>" || echo "nothing to commit"
```

---

## Self-Review notes (author's pre-flight)

- **Spec coverage:** thin-leaderboard output (Task 3 model); mirror-`posts_mentioning` matching (Task 2 Cypher + Task 7 lockstep); `count(DISTINCT p)` (Task 4); half-open window + entity-branch precedence (Task 5); resolved/unresolved + `audit_entities` (Task 6); all-`:Post`-types scope (Task 7 comment); no-merge (Task 8); one audit row (Task 9); UI page (Task 10); registry/agent/REST exposure via registration + conftest reload (Task 3); no router/client edits (registry-driven). All spec sections map to a task.
- **Deferred (not in this plan, by design):** engagement deltas, evidence snippets, entity-spanning matching.
- **Type consistency:** `AuthorsMentioningIn` / `AuthorMention` / `AuthorsMentioningOut` / `authors_mentioning` names are used identically across the tool module, tests, and UI. Cypher `RETURN` columns (`author_id, handle, display_name, platform, mention_post_count, first_mention, last_mention, entity_ids`) match the row keys read in the tool.
```
