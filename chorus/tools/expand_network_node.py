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
    """Return the next-hop neighbours of one network node; grows a network_around graph around a clicked node.

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
