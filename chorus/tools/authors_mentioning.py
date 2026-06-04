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
