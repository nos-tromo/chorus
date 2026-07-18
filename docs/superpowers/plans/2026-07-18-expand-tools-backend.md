# Expand Tools + Agent Graph Payload (chorus backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two audited registry tools (`expand_network_node`, `expand_social_node`) that return one hop of neighbors around a clicked graph node, and make `/agent/query` trace entries carry full graph-tool result payloads (size-capped).

**Architecture:** Both tools follow the six-file convention exactly (Cypher template + Pydantic I/O + `@audited` + registry + conftest + integration tests), reusing the node/edge output models from their sibling seed tools so the frontend merges results without translation. The agent loop's `TraceStep` gains an optional `result` field populated only for graph tools within a node-count cap.

**Tech Stack:** Python 3.12 (dev), FastAPI, Pydantic, Neo4j 5 (testcontainer for integration tests), pytest, uv.

**Repo:** ALL work happens in `/Users/himarc/dev/nos-tromo/infra/chorus`. Branch `feature/expand-tools` off `main`.

## Global Constraints

- Data confidentiality hard rule: all test fixtures use fully synthetic, invented placeholder names/handles/ids — never real data.
- Cypher lives in `chorus/queries/*.cypher`, never inline in Python.
- Every tool invocation goes through `@audited`; audit metadata via `audit_entities()` / `audit_result_count()`, audit-only data on a `PrivateAttr`.
- New tool modules MUST be added to `_CHORUS_ENV_MODULES` in `tests/conftest.py` (they self-register); forgetting this silently drops the tool from the registry after per-test reloads.
- The first docstring line of each tool function is the agent-facing description (enforced by `tests/tools/test_registry.py`) — one sentence, ends with guidance on when to use it.
- Integration tests need Docker (Neo4j testcontainer). Run them with `uv run pytest tests/integration -k <name>`.
- `make verify` (pre-commit: ruff + pyrefly, plus frontend lint/build) must be green before push; `git add` new files first (pre-commit is tracked-only).
- Frontend wiring for these tools is deliberately NOT in this plan — it is the whole of the companion frontend plan (`2026-07-18-reactive-graph-frontend.md`). The six-file convention's "React SPA screen" file is satisfied there.

---

### Task 1: `expand_network_node` tool

**Files:**
- Create: `chorus/queries/expand_network_node.cypher`
- Create: `chorus/tools/expand_network_node.py`
- Modify: `chorus/tools/__init__.py` (add import)
- Modify: `tests/conftest.py` (add `"chorus.tools.expand_network_node"` to `_CHORUS_ENV_MODULES`, next to `"chorus.tools.network_around"` at line ~41)
- Test: `tests/integration/test_expand_network_node.py`

**Interfaces:**
- Consumes: `NetworkNode`, `NetworkEdge` from `chorus.tools.network_around` (existing); `audited`, `register_tool` from `chorus.tools._audit`; `load_template` from `chorus.tools._template_loader`.
- Produces: registry tool `expand_network_node` with input `ExpandNetworkNodeIn {node_id: str, limit: int}` and output `ExpandNetworkNodeOut {nodes: list[NetworkNode], edges: list[NetworkEdge], truncated: bool}`. Node ids in the output are namespaced (`author:<id>` / `topic:<key>`) and exclude the clicked node itself; every edge has the clicked node's id as one endpoint. The frontend plan and the agent depend on exactly these names.

Semantics: `node_id` is one of the namespaced ids the seed tools emit. `author:<id>` → the topics that author mentions (edges author→topic, weight = distinct mentioning posts). `topic:<key>` → the authors who mention that topic (edges author→topic, same weight). `<key>` for a topic is the resolved `:Entity.id` when resolution ran, else the alias surface form — exactly the key `network_around` puts in `topic:<key>`, so a clicked topic node round-trips. Ranked by weight desc (deterministic tiebreak), capped at `limit`, `truncated` set when the cap dropped rows.

- [ ] **Step 1: Read the two sibling files first**

Read `chorus/tools/network_around.py` and `tests/integration/test_network_around.py` end to end before writing anything — the tool module below mirrors the former's structure, and your test file must reuse the latter's exact fixtures (driver/audit/user setup and any seed-data helpers). Align names with what you find; the test code in Step 5 states intent and assertions, not fixture spelling.

- [ ] **Step 2: Write the Cypher template**

Create `chorus/queries/expand_network_node.cypher`:

```cypher
// expand_network_node — one hop of the bipartite Author<->Topic mention graph
// around a single, already-rendered node. Powers expand-on-click in the SPA:
// the client sends a namespaced node id it got from network_around (or a prior
// expansion) and receives that node's next-hop neighbours only — the clicked
// node itself is NOT returned (the client already has it).
//
// $kind/'$key' are pre-split by the tool from the namespaced id:
//   kind='author', key=<:Author.id>   -> topics the author mentions
//   kind='topic',  key=<topic key>    -> authors mentioning the topic
// A topic key is the resolved :Entity.id when present, else the alias surface
// form — the same key network_around bakes into "topic:<key>", so a clicked
// topic node round-trips without a name lookup. Topic identity follows the
// coalesce(entity, alias) rule used across the graph tools.
//
// Bounding: ranked by weight (distinct mentioning posts) desc, deterministic
// tiebreak (topic key / author id asc), sliced to $limit in-query; `truncated`
// is true when the cap dropped rows. Exactly one row is always returned.

// ---- author kind: the topics this author mentions. ----
CALL {
  UNWIND CASE WHEN $kind = 'author' THEN [$key] ELSE [] END AS aid
  MATCH (a:Author {id: aid})-[:AUTHORED]->(p:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH p,
    CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END AS t_key,
    CASE WHEN m:Entity THEN m.canonical_name
         WHEN e IS NOT NULL THEN e.canonical_name
         ELSE m.surface_form END AS t_label,
    CASE WHEN m:Entity THEN m.id ELSE e.id END AS t_eid
  WHERE t_key IS NOT NULL
  WITH t_key, t_label, t_eid, count(DISTINCT p) AS weight
  ORDER BY weight DESC, t_key ASC
  RETURN collect({topic_key: t_key, topic_label: t_label,
                  topic_entity_id: t_eid, weight: weight}) AS topics_ranked
}

// ---- topic kind: the authors mentioning this topic (matched by key). ----
CALL {
  UNWIND CASE WHEN $kind = 'topic' THEN [$key] ELSE [] END AS tkey
  MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH a, p, tkey,
    CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END AS m_key
  WHERE m_key = tkey
  WITH a, count(DISTINCT p) AS weight
  ORDER BY weight DESC, a.id ASC
  RETURN collect({author_id: a.id, handle: a.handle,
                  display_name: a.display_name, weight: weight}) AS authors_ranked
}

RETURN
  topics_ranked[0..$limit]  AS topics,
  authors_ranked[0..$limit] AS authors,
  (size(topics_ranked) > $limit OR size(authors_ranked) > $limit) AS truncated;
```

- [ ] **Step 3: Write the tool module**

Create `chorus/tools/expand_network_node.py`:

```python
"""Tool: expand_network_node(node_id, limit).

Returns one hop of the bipartite Author<->Topic mention graph around a single,
already-rendered node — the expand-on-click primitive behind the SPA's network
explorer. Given a namespaced node id emitted by :mod:`chorus.tools.network_around`
(or a previous expansion), it returns that node's next-hop neighbours only:

- ``author:<id>``: the topics the author mentions (author -> topic edges).
- ``topic:<key>``: the authors who mention the topic (author -> topic edges).

The clicked node itself is not returned (the caller already renders it); every
edge has the clicked node id as one endpoint, so the client can merge the
result into its live graph without translation. Neighbours are ranked by
mention weight desc (deterministic tiebreak) and capped at ``limit``;
``truncated`` flags when the cap dropped rows.

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.network_around`, whose node/edge models it reuses.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template
from chorus.tools.network_around import NetworkEdge, NetworkNode

_NAMESPACES = ("author:", "topic:")


class ExpandNetworkNodeIn(BaseModel):
    """Input parameters for the ``expand_network_node`` tool.

    Attributes:
        node_id: Namespaced node id to expand — ``"author:<author_id>"`` or
            ``"topic:<key>"``, exactly as returned by ``network_around`` or a
            previous expansion. A topic key is the resolved entity id when
            resolution ran, else the alias surface form.
        limit: Maximum neighbours returned, in [1, 500], kept by descending
            mention weight (deterministic tiebreak).
    """

    node_id: str
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}

    @field_validator("node_id")
    @classmethod
    def _namespaced(cls, value: str) -> str:
        """Require a namespaced id with a non-empty key.

        Args:
            value: Proposed ``node_id``.

        Returns:
            ``value`` stripped, when valid.

        Raises:
            ValueError: When the id has no known namespace prefix or an
                empty key part.
        """
        trimmed = value.strip()
        if not trimmed.startswith(_NAMESPACES) or trimmed.split(":", 1)[1] == "":
            raise ValueError("node_id must be 'author:<id>' or 'topic:<key>'")
        return trimmed


class ExpandNetworkNodeOut(BaseModel):
    """Output of the ``expand_network_node`` tool.

    Attributes:
        nodes: The next-hop neighbour nodes (the clicked node is excluded).
        edges: Weighted Author -> Topic edges; the clicked node id is one
            endpoint of every edge.
        truncated: ``True`` when ``limit`` dropped neighbours.
    """

    nodes: list[NetworkNode]
    edges: list[NetworkEdge]
    truncated: bool
    # Resolved entity ids among the topic nodes, stashed for the §76 audit
    # trail — same convention as network_around.
    _entity_ids: list[str] = PrivateAttr(default_factory=list)

    def audit_entities(self) -> list[str]:
        """Return distinct resolved entity ids among the returned topic nodes.

        Returns:
            Entity ids in first-seen order, deduped; unresolved-alias topics
            contribute none, mirroring ``network_around``.
        """
        seen: list[str] = []
        for eid in self._entity_ids:
            if eid and eid not in seen:
                seen.append(eid)
        return seen

    def audit_result_count(self) -> int:
        """Return the number of neighbour nodes returned.

        Returns:
            Length of :attr:`nodes`.
        """
        return len(self.nodes)


@register_tool(
    name="expand_network_node",
    input_model=ExpandNetworkNodeIn,
    output_model=ExpandNetworkNodeOut,
)
@audited
def expand_network_node(
    driver: Driver,
    params: ExpandNetworkNodeIn,
    *,
    user: str,
    audit: AuditLogger,
) -> ExpandNetworkNodeOut:
    """Return the next-hop neighbours of one network node; use to grow an existing network_around graph around a clicked node.

    Splits the namespaced id, runs the ``expand_network_node.cypher`` template,
    and assembles the single result row into neighbour nodes plus edges anchored
    on the clicked node. The ``@audited`` decorator records the invocation.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        An :class:`ExpandNetworkNodeOut` with neighbour nodes, anchored edges,
        and a ``truncated`` flag.
    """
    del user, audit  # the @audited decorator owns the audit write
    kind, key = params.node_id.split(":", 1)
    cypher = load_template("expand_network_node")
    cypher_params: dict[str, Any] = {"kind": kind, "key": key, "limit": params.limit}
    with driver.session() as session:
        record = session.run(cypher, **cypher_params).single()

    if record is None:  # defensive: the query always yields one row
        return ExpandNetworkNodeOut(nodes=[], edges=[], truncated=False)

    nodes: list[NetworkNode] = []
    edges: list[NetworkEdge] = []

    for t in record["topics"]:
        topic_id = f"topic:{t['topic_key']}"
        nodes.append(
            NetworkNode(
                id=topic_id,
                kind="topic",
                label=t["topic_label"],
                entity_id=t["topic_entity_id"],
                is_seed=False,
            )
        )
        edges.append(NetworkEdge(source=params.node_id, target=topic_id, weight=t["weight"]))

    for a in record["authors"]:
        author_id = f"author:{a['author_id']}"
        nodes.append(
            NetworkNode(
                id=author_id,
                kind="author",
                label=a["handle"] or a["display_name"] or a["author_id"],
                entity_id=None,
                is_seed=False,
            )
        )
        edges.append(NetworkEdge(source=author_id, target=params.node_id, weight=a["weight"]))

    out = ExpandNetworkNodeOut(nodes=nodes, edges=edges, truncated=bool(record["truncated"]))
    out._entity_ids = [n.entity_id for n in nodes if n.kind == "topic" and n.entity_id]
    return out
```

- [ ] **Step 4: Register the module**

In `chorus/tools/__init__.py`, add (alphabetically among the existing imports):

```python
from chorus.tools import expand_network_node as expand_network_node  # noqa: F401
```

(match the exact import style used by the existing lines — read the file first and mirror it).

In `tests/conftest.py`, add to `_CHORUS_ENV_MODULES` directly after `"chorus.tools.authors_mentioning"`:

```python
    "chorus.tools.expand_network_node",
```

(Order constraint: it must appear before the aggregate `"chorus.tools"` entry, like the other tool modules.)

- [ ] **Step 5: Write the integration tests**

Create `tests/integration/test_expand_network_node.py`, mirroring the fixture usage of `tests/integration/test_network_around.py` (same driver/audit fixtures and seed helpers — adapt spelling to what that file actually uses). Test intent + assertions (all data fully synthetic):

```python
"""Integration tests for the expand_network_node tool (Neo4j testcontainer)."""

# Seed graph used by every test (create via the same session/seed pattern the
# sibling file uses):
#   (:Author {id: "auth-1", handle: "quietfjord"})
#   (:Author {id: "auth-2", handle: "mossyriver"})
#   (:Post:Posting {uuid: "post-1"}) ... (:Post:Posting {uuid: "post-3"})
#   auth-1 AUTHORED post-1, post-2; auth-2 AUTHORED post-3
#   (:Alias {surface_form: "glimmer initiative"}) mentioned by post-1, post-2, post-3
#   (:Alias {surface_form: "harbor works"}) mentioned by post-1
#   (:Alias {surface_form: "glimmer initiative"})-[:RESOLVED_TO]->(:Entity {id: "ent-1",
#       canonical_name: "Glimmer Initiative", type: "ORG"})


def test_author_expansion_returns_their_topics(...):
    # expand node_id="author:auth-1", limit=50
    # EXPECT: nodes contain topic:ent-1 (resolved key!) and topic:harbor works;
    #         every edge source == "author:auth-1"; edge to topic:ent-1 has weight 2
    #         (post-1 + post-2), edge to "topic:harbor works" weight 1; truncated False.


def test_topic_expansion_returns_mentioning_authors(...):
    # expand node_id="topic:ent-1" (the resolved key round-trips), limit=50
    # EXPECT: nodes are author:auth-1 (weight 2) and author:auth-2 (weight 1),
    #         labels are the handles; every edge target == "topic:ent-1"; not truncated.


def test_unresolved_topic_key_is_surface_form(...):
    # expand node_id="topic:harbor works"
    # EXPECT: exactly author:auth-1 returned — the surface-form key matches
    #         because "harbor works" has no RESOLVED_TO.


def test_limit_truncates_deterministically(...):
    # expand node_id="topic:ent-1", limit=1
    # EXPECT: exactly 1 node — author:auth-1 (higher weight ranks first);
    #         truncated True.


def test_unknown_node_yields_empty(...):
    # expand node_id="author:no-such-author"
    # EXPECT: nodes == [], edges == [], truncated False.


def test_bad_namespace_rejected(...):
    # ExpandNetworkNodeIn(node_id="banana") must raise pydantic.ValidationError.


def test_audit_row_written(...):
    # After a successful expansion, the audit log contains a row for
    # tool="expand_network_node" with result_count == len(nodes) — assert via
    # the same audit-inspection pattern the sibling test file uses.
```

Write these as real test functions with the sibling file's fixtures; each comment block above is the required behavior and the exact assertions to make.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/integration/test_expand_network_node.py -v` (Docker must be up)
Expected: PASS (7 tests). Also run `uv run pytest tests/tools/test_registry.py -v` — the registry test must now count the new tool and accept its docstring first line.

- [ ] **Step 7: Commit**

```bash
git add chorus/queries/expand_network_node.cypher chorus/tools/expand_network_node.py chorus/tools/__init__.py tests/conftest.py tests/integration/test_expand_network_node.py
git commit -m "feat: expand_network_node tool — one-hop expansion for the network explorer"
```

---

### Task 2: `expand_social_node` tool

**Files:**
- Create: `chorus/queries/expand_social_node.cypher`
- Create: `chorus/tools/expand_social_node.py`
- Modify: `chorus/tools/__init__.py` (add import)
- Modify: `tests/conftest.py` (add `"chorus.tools.expand_social_node"` to `_CHORUS_ENV_MODULES`)
- Test: `tests/integration/test_expand_social_node.py`

**Interfaces:**
- Consumes: `SocialEdge` from `chorus.tools.social_network_around` (existing).
- Produces: registry tool `expand_social_node` with input `ExpandSocialNodeIn {author_id: str, limit: int}` (raw `:Author.id`, NOT namespaced — the client strips the `author:` prefix) and output `ExpandSocialNodeOut {nodes: list[SocialNeighbor], edges: list[SocialEdge], truncated: bool}` where `SocialNeighbor {id: str, label: str}` (id namespaced `author:<id>`, no ring — the client assigns ring = clicked ring + 1). The frontend plan depends on exactly these names.

- [ ] **Step 1: Read the siblings**

Read `chorus/tools/social_network_around.py`, `chorus/queries/social_network_around.cypher`, and `tests/integration/test_social_network_around.py` before writing — the Cypher below is that query's ring-1 block seeded by id, and the tests reuse its fixtures.

- [ ] **Step 2: Write the Cypher template**

Create `chorus/queries/expand_social_node.cypher`:

```cypher
// expand_social_node — the direct social ties (:FOLLOWS directed,
// :FRIENDS_WITH undirected) of one author, seeded by :Author.id. Powers
// expand-on-click in the SPA's social explorer: the ring-1 block of
// social_network_around, seeded by id instead of name, returning the clicked
// author's neighbours + the connecting edges only (the clicked author is not
// re-returned).
//
// Edge identity is intrinsic to the relationship, matching
// social_network_around verbatim:
//   :FOLLOWS      -> directed, src = startNode (follower), dst = endNode (followee)
//   :FRIENDS_WITH -> undirected, emitted canonically (lower id = src), directed=false
//
// Bounding: neighbours ranked by social degree desc (tiebreak author id asc),
// sliced to $limit in-query; `truncated` true when the cap dropped nodes.
// Yields exactly one row when the author exists, zero rows otherwise.

MATCH (seed:Author {id: $author_id})

CALL (seed) {
  MATCH (seed)-[:FOLLOWS|FRIENDS_WITH]-(nb:Author)
  WHERE nb <> seed
  WITH DISTINCT nb
  WITH nb, COUNT { (nb)-[:FOLLOWS|FRIENDS_WITH]-() } AS deg
  ORDER BY deg DESC, nb.id ASC
  RETURN collect(nb.id) AS ranked
}
WITH seed,
     ranked[0..$limit]     AS kept,
     size(ranked) > $limit AS truncated

CALL (seed, kept) {
  MATCH (seed)-[r:FOLLOWS|FRIENDS_WITH]-(b:Author)
  WHERE b.id IN kept
  WITH DISTINCT r, b
  RETURN collect({
    src:      CASE WHEN type(r) = 'FOLLOWS' THEN startNode(r).id
                   WHEN seed.id < b.id THEN seed.id ELSE b.id END,
    dst:      CASE WHEN type(r) = 'FOLLOWS' THEN endNode(r).id
                   WHEN seed.id < b.id THEN b.id ELSE seed.id END,
    kind:     CASE WHEN type(r) = 'FOLLOWS' THEN 'follows' ELSE 'friends' END,
    directed: type(r) = 'FOLLOWS'
  }) AS edges
}

CALL (kept) {
  UNWIND kept AS rid
  MATCH (a:Author {id: rid})
  RETURN collect({id: a.id, handle: a.handle, display_name: a.display_name}) AS neighbours
}

RETURN neighbours, edges, truncated;
```

- [ ] **Step 3: Write the tool module**

Create `chorus/tools/expand_social_node.py`:

```python
"""Tool: expand_social_node(author_id, limit).

Returns the direct social ties (``:FOLLOWS``/``:FRIENDS_WITH``) of one author,
seeded by ``:Author.id`` — the expand-on-click primitive behind the SPA's
social explorer. It is the ring-1 block of
:mod:`chorus.tools.social_network_around` seeded by id instead of name: the
clicked author is not re-returned, neighbours carry no ring (the client
assigns ring = clicked ring + 1), and every edge connects the clicked author
to a neighbour, ready to merge into the live graph.

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.social_network_around`, whose edge model it reuses.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template
from chorus.tools.social_network_around import SocialEdge


class ExpandSocialNodeIn(BaseModel):
    """Input parameters for the ``expand_social_node`` tool.

    Attributes:
        author_id: Raw ``:Author.id`` of the node to expand (the client strips
            the ``author:`` namespace prefix before calling).
        limit: Maximum neighbours returned, in [1, 500], kept by descending
            social degree (tiebreak author id).
    """

    author_id: str
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}

    @field_validator("author_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Require a non-empty author id.

        Args:
            value: Proposed ``author_id``.

        Returns:
            ``value`` stripped, when non-empty.

        Raises:
            ValueError: When the id is empty or whitespace.
        """
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("author_id must be non-empty")
        return trimmed


class SocialNeighbor(BaseModel):
    """A neighbour author returned by an expansion.

    Attributes:
        id: Namespaced node id (``"author:<author_id>"``).
        label: Display name — handle, falling back to display name, then id.
    """

    id: str
    label: str


class ExpandSocialNodeOut(BaseModel):
    """Output of the ``expand_social_node`` tool.

    Attributes:
        nodes: The clicked author's direct ties (the clicked author excluded).
        edges: Social ties connecting the clicked author to each neighbour,
            each carrying ``kind``/``directed``.
        truncated: ``True`` when ``limit`` dropped neighbours.
    """

    nodes: list[SocialNeighbor]
    edges: list[SocialEdge]
    truncated: bool
    # Author ids touched, for the §76 audit trail — same deliberate convention
    # as social_network_around (persons accessed, not entities).
    _author_ids: list[str] = PrivateAttr(default_factory=list)

    def audit_entities(self) -> list[str]:
        """Return the distinct author ids touched (the persons accessed).

        Returns:
            Author ids in first-seen order, deduped, including the clicked
            author.
        """
        seen: list[str] = []
        for aid in self._author_ids:
            if aid and aid not in seen:
                seen.append(aid)
        return seen

    def audit_result_count(self) -> int:
        """Return the number of neighbour nodes returned.

        Returns:
            Length of :attr:`nodes`.
        """
        return len(self.nodes)


@register_tool(
    name="expand_social_node",
    input_model=ExpandSocialNodeIn,
    output_model=ExpandSocialNodeOut,
)
@audited
def expand_social_node(
    driver: Driver,
    params: ExpandSocialNodeIn,
    *,
    user: str,
    audit: AuditLogger,
) -> ExpandSocialNodeOut:
    """Return the direct follows/friends ties of one author by id; use to grow an existing social_network_around graph around a clicked node.

    Runs the ``expand_social_node.cypher`` template and assembles the single
    result row into neighbour nodes plus connecting edges. The ``@audited``
    decorator records the invocation.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        An :class:`ExpandSocialNodeOut` with neighbour nodes, typed social
        edges, and a ``truncated`` flag.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("expand_social_node")
    cypher_params: dict[str, Any] = {"author_id": params.author_id, "limit": params.limit}
    with driver.session() as session:
        record = session.run(cypher, **cypher_params).single()

    if record is None:
        # Unknown author id: empty expansion.
        return ExpandSocialNodeOut(nodes=[], edges=[], truncated=False)

    nodes = [
        SocialNeighbor(
            id=f"author:{rec['id']}",
            label=rec.get("handle") or rec.get("display_name") or str(rec["id"]),
        )
        for rec in record["neighbours"]
    ]
    edges = [
        SocialEdge(
            source=f"author:{e['src']}",
            target=f"author:{e['dst']}",
            kind=e["kind"],
            directed=e["directed"],
        )
        for e in record["edges"]
    ]

    out = ExpandSocialNodeOut(nodes=nodes, edges=edges, truncated=bool(record["truncated"]))
    out._author_ids = [params.author_id, *[n.id.removeprefix("author:") for n in nodes]]
    return out
```

- [ ] **Step 4: Register the module**

`chorus/tools/__init__.py`: add the import next to Task 1's. `tests/conftest.py`: add `"chorus.tools.expand_social_node"` directly after `"chorus.tools.expand_network_node"`.

- [ ] **Step 5: Write the integration tests**

Create `tests/integration/test_expand_social_node.py` with the sibling file's fixtures. Seed (synthetic): authors `auth-a` (handle `sablecliff`), `auth-b` (handle `fernhollow`), `auth-c` (handle `driftgate`); edges `auth-b FOLLOWS auth-a`, `auth-a FOLLOWS auth-c`, `auth-a FRIENDS_WITH auth-b` (stored `auth-a`→`auth-b`, canonical lower-id direction). Tests:

```python
def test_expansion_returns_direct_ties(...):
    # expand author_id="auth-a", limit=50
    # EXPECT nodes: author:auth-b, author:auth-c (labels = handles).
    # EXPECT edges: {author:auth-b -> author:auth-a, kind follows, directed True},
    #               {author:auth-a -> author:auth-c, kind follows, directed True},
    #               {author:auth-a -> author:auth-b, kind friends, directed False}.
    # truncated False.

def test_limit_truncates_by_degree(...):
    # expand author_id="auth-a", limit=1
    # EXPECT exactly 1 node (auth-b: degree 2 beats auth-c: degree 1); truncated True.

def test_unknown_author_yields_empty(...):
    # expand author_id="no-such" -> nodes [], edges [], truncated False.

def test_empty_author_id_rejected(...):
    # ExpandSocialNodeIn(author_id="   ") raises pydantic.ValidationError.

def test_audit_records_author_ids(...):
    # After expanding auth-a: the audit row's entities include auth-a, auth-b,
    # auth-c (persons accessed), result_count == 2 — assert via the sibling
    # file's audit-inspection pattern.
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/integration/test_expand_social_node.py tests/tools/test_registry.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add chorus/queries/expand_social_node.cypher chorus/tools/expand_social_node.py chorus/tools/__init__.py tests/conftest.py tests/integration/test_expand_social_node.py
git commit -m "feat: expand_social_node tool — one-hop social expansion for the explorer"
```

---

### Task 3: Graph payloads in the agent trace

**Files:**
- Modify: `chorus/agent/loop.py` (TraceStep + `_execute_tool_call`)
- Test: extend the existing agent-loop unit tests (find them with `grep -rl "TraceStep" tests/` — expected `tests/agent/test_loop.py`; add to that file)

**Interfaces:**
- Consumes: the `result` dict already computed in `_execute_tool_call` (`out.model_dump(mode="json")`).
- Produces: `TraceStep.result: dict[str, Any] | None` — populated ONLY for the four graph tools and ONLY when the payload has ≤ 500 nodes. `/agent/query` needs no change (it serializes `AgentResult` verbatim, so `result` flows to the client automatically). The frontend plan renders `trace[i].result` when non-null.

- [ ] **Step 1: Write the failing tests**

In the agent-loop test file (mirror its existing fake-provider/tool setup — read it first), add:

```python
def test_trace_carries_graph_tool_result(...):
    # Arrange the fake provider to call network_around (or a registered fake
    # graph tool whose name is in the graph set) returning
    # {"nodes": [...2 items...], "edges": [...], "truncated": False}.
    # EXPECT: result.trace[0].result == that full payload (not compacted).

def test_trace_omits_result_for_non_graph_tools(...):
    # Fake call to posts_mentioning.
    # EXPECT: result.trace[0].result is None.

def test_trace_omits_oversized_graph_result(...):
    # Fake network_around result with 501 nodes.
    # EXPECT: result.trace[0].result is None, but result_count still set.

def test_trace_result_none_on_tool_error(...):
    # Fake an unknown-tool call.
    # EXPECT: trace step has error set and result None.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent -v -k "trace"`
Expected: FAIL — `TraceStep` has no field `result`.

- [ ] **Step 3: Implement**

In `chorus/agent/loop.py`:

1. Below the existing module constants (`_MAX_TOOL_MESSAGE_ITEMS`, line ~28), add:

```python
# Graph tools whose full node/edge payload is echoed on the trace so the SPA
# can render the graph inline (wiring (a) of the reactive-graph design). The
# node cap keeps one huge graph from ballooning the /agent/query response;
# result_count still reports the true size when the payload is withheld.
_GRAPH_RESULT_TOOLS = frozenset(
    {"network_around", "social_network_around", "expand_network_node", "expand_social_node"}
)
_MAX_TRACE_GRAPH_NODES = 500
```

2. Extend `TraceStep` (docstring attribute list too):

```python
    result: dict[str, Any] | None = None
```

with the attribute doc line: `result: Full tool output for graph tools (node/edge payloads the SPA renders inline), when within the size cap; None otherwise.`

3. In `_execute_tool_call`, replace the success return (currently `return (TraceStep(tool=name, arguments=arguments, result_count=result_count), _tool_message(...))`) with:

```python
    trace_result: dict[str, Any] | None = None
    if name in _GRAPH_RESULT_TOOLS:
        nodes = result.get("nodes")
        if isinstance(nodes, list) and len(nodes) <= _MAX_TRACE_GRAPH_NODES:
            trace_result = result
    return (
        TraceStep(tool=name, arguments=arguments, result_count=result_count, result=trace_result),
        _tool_message(tc, result, result_count=result_count, max_items=max_items, max_chars=max_chars),
    )
```

Note: the tool message fed back to the model still goes through `_compact_tool_content` unchanged — the full payload rides only on the API trace, never into the model context.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent -v`
Expected: PASS (new + all existing).

- [ ] **Step 5: Commit**

```bash
git add chorus/agent/loop.py tests/agent/test_loop.py
git commit -m "feat: carry graph-tool payloads on the agent trace (size-capped)"
```

---

### Task 4: Full verification + PR

- [ ] **Step 1: Full test suite**

Run: `uv run pytest`
Expected: PASS — pay attention to full-suite-only failures from the conftest module list (a class-identity break here means an entry was added that shouldn't be, or vice versa).

- [ ] **Step 2: make verify**

Run: `make verify`
Expected: green (ruff, pyrefly, frontend lint/build untouched by this PR but included in the gate).

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feature/expand-tools
gh pr create --title "feat: expand-on-click tools + agent graph payloads" --body "Backend half of the reactive graph exploration design (docs/superpowers/specs/2026-07-18-reactive-graph-exploration-design.md): expand_network_node + expand_social_node registry tools (one-hop, audited, agent-callable) and size-capped graph payloads on /agent/query trace entries. Frontend adoption follows in a separate PR."
```
