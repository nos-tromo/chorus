"""Tool: network_around(entity, depth, limit, topic_limit).

Returns the bipartite Author<->Topic ego network around a seed topic as a
renderer-ready node/edge list — the visual companion to the tabular graph tools.
Topic identity follows the coalesce(entity, alias) rule used across the graph
tools, so the network improves automatically once entity resolution lands.

Rings, from the seed topic S:

- ``depth=1``: S + the authors who mention it (a star — who talks about X).
- ``depth=2``: + the other topics those authors mention (topic -> authors ->
  co-topics).

v1 supports ``depth`` 1 and 2 only; ``depth > 2`` is rejected at input validation
(surfaced as HTTP 422 by the tools router). The author ring is bounded by
``limit`` and the second-ring topics by ``topic_limit``; ``truncated`` flags when
either cap dropped nodes.

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.authors_mentioning`.
"""

from __future__ import annotations

from typing import Any, Literal

from neo4j import Driver
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


class NetworkAroundIn(BaseModel):
    """Input parameters for the ``network_around`` tool.

    Attributes:
        entity: Entity canonical name or unresolved alias surface form to
            seed the network. Matching is case-insensitive and identical to
            ``posts_mentioning`` / ``authors_mentioning``.
        depth: Ring count. ``1`` returns the seed plus the authors who mention
            it; ``2`` additionally returns the other topics those authors
            mention. v1 supports 1 and 2 only; larger values are rejected at
            validation time.
        limit: Maximum authors in the first ring, in [1, 200], kept by
            descending seed mention count (tiebreak author id).
        topic_limit: Maximum second-ring topics, in [1, 500], kept by
            descending total edge weight (tiebreak topic key). Ignored at
            ``depth=1``.
    """

    entity: str
    depth: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=200)
    topic_limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}

    @field_validator("depth")
    @classmethod
    def _depth_supported(cls, value: int) -> int:
        """Reject ``depth`` greater than 2 (deeper traversal is not yet supported).

        Args:
            value: Proposed ``depth`` value.

        Returns:
            ``value`` unchanged when supported.

        Raises:
            ValueError: When ``value`` exceeds 2.
        """
        if value > 2:
            raise ValueError("depth > 2 not yet supported")
        return value


class NetworkNode(BaseModel):
    """A node in the returned network.

    Attributes:
        id: Globally-unique, namespaced node id (``"topic:<key>"`` or
            ``"author:<author_id>"``) so a renderer never confuses an author
            id with a topic surface form.
        kind: Node kind — ``"topic"`` or ``"author"``.
        label: Display name — a topic's canonical name / surface form, or an
            author's handle (falling back to display name, then id).
        entity_id: Resolved ``:Entity.id`` for a topic node, or ``None`` for an
            unresolved-alias topic and for every author node.
        is_seed: ``True`` only for the seed topic node.
    """

    id: str
    kind: Literal["topic", "author"]
    label: str
    entity_id: str | None
    is_seed: bool


class NetworkEdge(BaseModel):
    """A weighted Author -> Topic "mentions" edge.

    Attributes:
        source: Node id of the author endpoint.
        target: Node id of the topic endpoint.
        weight: Number of distinct posts by the author that mention the topic.
    """

    source: str
    target: str
    weight: int


class NetworkAroundOut(BaseModel):
    """Output of the ``network_around`` tool.

    Attributes:
        seed: Matched seed display name (echo of the resolved seed topic), or
            the trimmed query when nothing matched.
        seed_node_id: Node id of the seed topic, or ``None`` when the seed
            matched nothing (empty network).
        nodes: Topic and author nodes in the network.
        edges: Weighted Author -> Topic edges.
        truncated: ``True`` when ``limit`` or ``topic_limit`` dropped nodes, so
            the drawn network is a capped view.
    """

    seed: str
    seed_node_id: str | None
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]
    truncated: bool
    # Resolved entity ids among the topic nodes, stashed for the §76 audit
    # trail. Private so the public/JSON shape stays the node/edge graph.
    _entity_ids: list[str] = PrivateAttr(default_factory=list)

    def audit_entities(self) -> list[str]:
        """Return distinct resolved entity ids among the topic nodes.

        Returns:
            Entity ids in first-seen order, deduped. Unresolved-alias topics
            contribute none (they have no canonical id yet), mirroring the
            sibling graph tools.
        """
        seen: list[str] = []
        for eid in self._entity_ids:
            if eid and eid not in seen:
                seen.append(eid)
        return seen

    def audit_result_count(self) -> int:
        """Return the size of the network (number of nodes).

        Returns:
            Length of :attr:`nodes`.
        """
        return len(self.nodes)


def _topic_node_id(key: str) -> str:
    """Return the namespaced node id for a topic key.

    Args:
        key: Topic key (resolved entity id or alias surface form).

    Returns:
        The ``"topic:<key>"`` node id.
    """
    return f"topic:{key}"


def _author_node_id(author_id: str) -> str:
    """Return the namespaced node id for an author.

    Args:
        author_id: Canonical ``:Author.id``.

    Returns:
        The ``"author:<author_id>"`` node id.
    """
    return f"author:{author_id}"


def _pick_seed(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick one representative seed variant deterministically.

    The seed query can match several mention variants (e.g. a resolved entity
    plus an unresolved alias of the same surface form). Prefer a resolved
    variant so the seed clusters on its canonical entity; break ties by key.

    Args:
        variants: Candidate ``{key, label, entity_id}`` maps from the query.

    Returns:
        The chosen variant, or ``None`` when ``variants`` is empty.
    """
    if not variants:
        return None
    resolved = [v for v in variants if v.get("entity_id")]
    pool = resolved if resolved else variants
    return min(pool, key=lambda v: str(v["key"]))


@register_tool(
    name="network_around",
    input_model=NetworkAroundIn,
    output_model=NetworkAroundOut,
)
@audited
def network_around(
    driver: Driver,
    params: NetworkAroundIn,
    *,
    user: str,
    audit: AuditLogger,
) -> NetworkAroundOut:
    """Return the author-topic network around an entity for visualization (use the tabular tools to rank).

    Loads the ``network_around.cypher`` template and runs it, then assembles the
    single result row into a renderer-agnostic node/edge graph. The ``@audited``
    decorator records the invocation; this function owns query execution and
    result shaping only.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        A :class:`NetworkAroundOut` with the seed topic, author/topic nodes,
        weighted edges, and a ``truncated`` flag.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("network_around")
    cypher_params: dict[str, Any] = {
        "entity": params.entity,
        "depth": params.depth,
        "limit": params.limit,
        "topic_limit": params.topic_limit,
    }
    with driver.session() as session:
        record = session.run(cypher, **cypher_params).single()

    if record is None:
        return NetworkAroundOut(
            seed=params.entity.strip(),
            seed_node_id=None,
            nodes=[],
            edges=[],
            truncated=False,
        )

    seed_variants = list(record["seed_variants"])
    ring1 = list(record["ring1"])
    raw_edges = list(record["edges"])
    truncated = bool(record["truncated"])

    nodes: dict[str, NetworkNode] = {}
    edges: list[NetworkEdge] = []

    seed = _pick_seed(seed_variants)
    if seed is None:
        # Seed matched nothing: empty network (no authors either).
        return NetworkAroundOut(
            seed=params.entity.strip(),
            seed_node_id=None,
            nodes=[],
            edges=[],
            truncated=truncated,
        )

    seed_node_id = _topic_node_id(seed["key"])
    nodes[seed_node_id] = NetworkNode(
        id=seed_node_id,
        kind="topic",
        label=seed["label"],
        entity_id=seed["entity_id"],
        is_seed=True,
    )

    for r in ring1:
        node_id = _author_node_id(r["author_id"])
        nodes[node_id] = NetworkNode(
            id=node_id,
            kind="author",
            label=r["handle"] or r["display_name"] or r["author_id"],
            entity_id=None,
            is_seed=False,
        )

    if params.depth >= 2:
        for ed in raw_edges:
            topic_id = _topic_node_id(ed["topic_key"])
            if topic_id not in nodes:
                nodes[topic_id] = NetworkNode(
                    id=topic_id,
                    kind="topic",
                    label=ed["topic_label"],
                    entity_id=ed["topic_entity_id"],
                    is_seed=False,
                )
            edges.append(
                NetworkEdge(
                    source=_author_node_id(ed["author_id"]),
                    target=topic_id,
                    weight=ed["weight"],
                )
            )
    else:
        # depth 1: the star — every ring-1 author links to the seed.
        for r in ring1:
            edges.append(
                NetworkEdge(
                    source=_author_node_id(r["author_id"]),
                    target=seed_node_id,
                    weight=r["w_seed"],
                )
            )

    node_list = list(nodes.values())
    out = NetworkAroundOut(
        seed=seed["label"],
        seed_node_id=seed_node_id,
        nodes=node_list,
        edges=edges,
        truncated=truncated,
    )
    out._entity_ids = [n.entity_id for n in node_list if n.kind == "topic" and n.entity_id]
    return out
