"""Reference tool: posts_mentioning(entity, from, to, limit).

Pattern: Pydantic input + Cypher template + `@audited` wrapper. Every
follow-on tool should look like this — single responsibility, no inline
Cypher, the audit event written by the decorator from the ``audit_*``
methods of the output model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field, PrivateAttr

from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


class PostsMentioningIn(BaseModel):
    """Input parameters for the ``posts_mentioning`` tool.

    The ``from`` alias keeps the public name aligned with the
    documented tool surface even though ``from`` is a Python keyword.

    Attributes:
        entity: Entity canonical name or unresolved alias surface form
            to filter posts by. Matching is case-insensitive.
        from_: Inclusive lower bound on the post timestamp. ``None``
            means no lower bound.
        to: Inclusive upper bound on the post timestamp. ``None``
            means no upper bound.
        limit: Maximum number of hits to return, in [1, 500].
    """

    entity: str
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}


class PostsMentioningHit(BaseModel):
    """A single match returned by ``posts_mentioning``.

    Attributes:
        uuid: Canonical chorus identifier for the post.
        text: Body of the matching post.
        ts: Post timestamp (content creation time).
        labels: Multi-label set on the post node (``["Post", "Posting"]``,
            ``["Post", "Comment"]``, ``["Post", "Message"]``).
        entity_id: Canonical id of the matched entity when the mention
            has already been resolved, otherwise ``None`` for alias-only
            hits.
        matched_name: Canonical entity name or alias surface form that
            matched the query.
    """

    uuid: str
    text: str
    ts: datetime
    labels: list[str]
    entity_id: str | None
    matched_name: str


class PostsMentioningOut(BaseModel):
    """Output of the ``posts_mentioning`` tool.

    Attributes:
        hits: Matching posts in result order.
    """

    hits: list[PostsMentioningHit]
    # Authors of the returned posts, stashed for the §76 audit trail. Private
    # so the public/JSON shape stays ``{"hits": [...]}``.
    _author_ids: list[str] = PrivateAttr(default_factory=list)

    def audit_authors(self) -> list[str]:
        """Return the ids of the authors whose posts this result discloses.

        Returns:
            Author and co-author ids, in result order. The decorator
            deduplicates them.
        """
        return list(self._author_ids)

    def audit_entities(self) -> list[str]:
        """Return distinct resolved entity ids touched by this result set.

        Returns:
            Entity ids in first-seen order, deduped. Unresolved alias
            hits are omitted because they do not yet have canonical ids.
        """
        seen: list[str] = []
        for h in self.hits:
            if h.entity_id and h.entity_id not in seen:
                seen.append(h.entity_id)
        return seen

    def audit_posts(self) -> list[str]:
        """Return the uuids of the posts this result discloses.

        Returns:
            Post uuids in result order.
        """
        return [h.uuid for h in self.hits]

    def audit_result_count(self) -> int:
        """Return the number of hits in this result set.

        Returns:
            Length of :attr:`hits`.
        """
        return len(self.hits)


@register_tool(
    name="posts_mentioning",
    input_model=PostsMentioningIn,
    output_model=PostsMentioningOut,
)
@audited
def posts_mentioning(driver: Driver, params: PostsMentioningIn) -> PostsMentioningOut:
    """Return posts mentioning ``params.entity`` within an optional time range.

    Loads the ``posts_mentioning.cypher`` template and runs it against
    the graph. The ``@audited`` decorator records the invocation; this
    function only owns the query execution and result shaping.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.

    Returns:
        A populated :class:`PostsMentioningOut` with at most
        ``params.limit`` hits.
    """
    cypher = load_template("posts_mentioning")
    cypher_params: dict[str, Any] = {
        "entity": params.entity,
        "from": params.from_.isoformat() if params.from_ else None,
        "to": params.to.isoformat() if params.to else None,
        "limit": params.limit,
    }
    hits: list[PostsMentioningHit] = []
    author_ids: list[str] = []
    with driver.session() as session:
        for row in session.run(cypher, **cypher_params):
            hits.append(
                PostsMentioningHit(
                    uuid=row["uuid"],
                    text=row["text"],
                    ts=row["ts"].to_native() if hasattr(row["ts"], "to_native") else row["ts"],
                    labels=list(row["labels"]),
                    entity_id=row["entity_id"],
                    matched_name=row["matched_name"],
                )
            )
            author_ids.extend(row["author_ids"])
    out = PostsMentioningOut(hits=hits)
    out._author_ids = author_ids
    return out
