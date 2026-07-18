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
    """Return one author's direct ties by id; grows a social_network_around graph around a clicked node.

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
