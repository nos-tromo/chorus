"""Tool: social_network_around(author, depth, limit, second_ring_limit).

Returns the author **ego network** over the social graph (`:FOLLOWS` directed,
`:FRIENDS_WITH` undirected) as a renderer-ready node/edge list — the social twin
of :mod:`chorus.tools.network_around` (the *topic* ego network). Seeds on an
`:Author`; traverses social ties, not mentions.

Rings, from the seed author S:

- ``depth=1``: S + its direct ties (people S follows, people who follow S,
  friends).
- ``depth=2``: + the ties of those neighbours (radial ring-1 -> ring-2 edges).

v1 supports ``depth`` 1 and 2 only; ``depth > 2`` is rejected at input validation
(surfaced as HTTP 422 by the tools router). Ring 1 is bounded by ``limit`` and
ring 2 by ``second_ring_limit``; ``truncated`` flags when either cap dropped
nodes. Each edge carries its ``kind`` (``follows``/``friends``) and ``directed``
flag so a renderer can draw arrowheads for follows and plain lines for friends.

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.network_around`.
"""

from __future__ import annotations

from typing import Any, Literal

from neo4j import Driver
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


class SocialNetworkAroundIn(BaseModel):
    """Input parameters for the ``social_network_around`` tool.

    Attributes:
        author: Seed author handle or display name, matched case-insensitively
            (identical to ``authors_connected_by_topic``'s ``seed_author``). On
            an ambiguous name the tool builds the ego network around one
            author, picked deterministically (handle match preferred, then
            lowest id).
        depth: Ring count. ``1`` returns the seed plus its direct social ties;
            ``2`` additionally returns the ties of those neighbours. v1 supports
            1 and 2 only; larger values are rejected at validation time.
        limit: Maximum ring-1 (direct) neighbours, in [1, 200], kept by
            descending social degree (tiebreak author id).
        second_ring_limit: Maximum ring-2 neighbours, in [1, 500], kept by
            descending social degree (tiebreak author id). Ignored at
            ``depth=1``.
    """

    author: str
    depth: int = Field(default=1, ge=1)
    limit: int = Field(default=25, ge=1, le=200)
    second_ring_limit: int = Field(default=50, ge=1, le=500)

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


class SocialNode(BaseModel):
    """An author node in the returned network.

    Attributes:
        id: Namespaced node id (``"author:<author_id>"``) so a renderer never
            confuses it with another id space.
        label: Display name — the author's handle, falling back to display name,
            then the raw id.
        ring: Hop distance from the seed — ``0`` (seed), ``1`` (direct tie), or
            ``2`` (tie of a tie).
        is_seed: ``True`` only for the seed author node (``ring == 0``).
    """

    id: str
    label: str
    ring: int
    is_seed: bool


class SocialEdge(BaseModel):
    """A social tie between two author nodes.

    Attributes:
        source: Node id of the source endpoint. For a follow, the follower; for
            a friendship, the lower-id endpoint (canonical order).
        target: Node id of the target endpoint. For a follow, the followee.
        kind: ``"follows"`` (directed) or ``"friends"`` (undirected).
        directed: ``True`` for a follow (draw an arrowhead source -> target),
            ``False`` for a friendship (draw a plain line).
    """

    source: str
    target: str
    kind: Literal["follows", "friends"]
    directed: bool


class SocialNetworkAroundOut(BaseModel):
    """Output of the ``social_network_around`` tool.

    Attributes:
        seed: Matched seed author label (handle/display name), or the trimmed
            query when nothing matched.
        seed_node_id: Node id of the seed author, or ``None`` when the seed
            matched nothing (empty network).
        nodes: Author nodes in the network, tagged with their ``ring``.
        edges: Social ties (follows/friends), each carrying ``kind``/``directed``.
        truncated: ``True`` when ``limit`` or ``second_ring_limit`` dropped
            nodes, so the drawn network is a capped view.
    """

    seed: str
    seed_node_id: str | None
    nodes: list[SocialNode]
    edges: list[SocialEdge]
    truncated: bool
    # Author ids in the network, stashed for the §76 audit trail. Unlike the
    # mention tools (which record resolved :Entity ids), a social read touches no
    # :Entity — the audit-relevant "entities" are the persons whose connection
    # data was accessed, so this records author ids. Deliberate, not accidental.
    _author_ids: list[str] = PrivateAttr(default_factory=list)

    def audit_entities(self) -> list[str]:
        """Return the distinct author ids in the network (the persons touched).

        Returns:
            Author ids in first-seen order, deduped. A deliberate divergence
            from the mention tools, which record resolved entity ids: a social
            read accesses people, and those are what §76 should capture here.
        """
        seen: list[str] = []
        for aid in self._author_ids:
            if aid and aid not in seen:
                seen.append(aid)
        return seen

    def audit_result_count(self) -> int:
        """Return the size of the network (number of nodes).

        Returns:
            Length of :attr:`nodes`.
        """
        return len(self.nodes)


def _author_node_id(author_id: str) -> str:
    """Return the namespaced node id for an author.

    Args:
        author_id: Canonical ``:Author.id``.

    Returns:
        The ``"author:<author_id>"`` node id.
    """
    return f"author:{author_id}"


def _label(rec: dict[str, Any]) -> str:
    """Pick a display label for an author record (handle, then display name, then id).

    Args:
        rec: An ``{id, handle, display_name}`` map from the query.

    Returns:
        The best available label.
    """
    return rec.get("handle") or rec.get("display_name") or str(rec["id"])


@register_tool(
    name="social_network_around",
    input_model=SocialNetworkAroundIn,
    output_model=SocialNetworkAroundOut,
)
@audited
def social_network_around(
    driver: Driver,
    params: SocialNetworkAroundIn,
    *,
    user: str,
    audit: AuditLogger,
) -> SocialNetworkAroundOut:
    """Return the follows/friends network around an author; use when asked who is connected to a person.

    Loads the ``social_network_around.cypher`` template and runs it, then
    assembles the single result row into a renderer-agnostic node/edge graph. The
    ``@audited`` decorator records the invocation; this function owns query
    execution and result shaping only.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        A :class:`SocialNetworkAroundOut` with the seed author, ring-tagged
        author nodes, typed social edges, and a ``truncated`` flag.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("social_network_around")
    cypher_params: dict[str, Any] = {
        "author": params.author,
        "depth": params.depth,
        "limit": params.limit,
        "second_ring_limit": params.second_ring_limit,
    }
    with driver.session() as session:
        record = session.run(cypher, **cypher_params).single()

    if record is None:
        # Seed matched nothing: empty network.
        return SocialNetworkAroundOut(
            seed=params.author.strip(),
            seed_node_id=None,
            nodes=[],
            edges=[],
            truncated=False,
        )

    seed_rec: dict[str, Any] = {
        "id": record["seed_id"],
        "handle": record["seed_handle"],
        "display_name": record["seed_display_name"],
    }
    seed_node_id = _author_node_id(seed_rec["id"])

    nodes: list[SocialNode] = [SocialNode(id=seed_node_id, label=_label(seed_rec), ring=0, is_seed=True)]
    nodes.extend(
        SocialNode(id=_author_node_id(rec["id"]), label=_label(rec), ring=1, is_seed=False) for rec in record["ring1"]
    )
    nodes.extend(
        SocialNode(id=_author_node_id(rec["id"]), label=_label(rec), ring=2, is_seed=False) for rec in record["ring2"]
    )

    edges: list[SocialEdge] = [
        SocialEdge(
            source=_author_node_id(e["src"]),
            target=_author_node_id(e["dst"]),
            kind=e["kind"],
            directed=e["directed"],
        )
        for e in record["edges"]
    ]

    out = SocialNetworkAroundOut(
        seed=_label(seed_rec),
        seed_node_id=seed_node_id,
        nodes=nodes,
        edges=edges,
        truncated=bool(record["truncated"]),
    )
    out._author_ids = [n.id.removeprefix("author:") for n in nodes]
    return out
