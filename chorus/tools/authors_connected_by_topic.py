"""Tool: authors_connected_by_topic(seed_author, min_overlap, max_hops, limit).

Finds other authors who mention the same topics as a seed author, ranked by how
many topics they share (the overlap). Topic identity follows the
coalesce(entity, alias) rule used across the graph tools, so connections improve
automatically once entity resolution lands. A seed name may match several
authors; results are grouped per matched seed so distinct people are never
silently merged.

Only 1-hop connections are supported in v1; ``max_hops > 1`` is rejected at input
validation (surfaced as HTTP 422 by the tools router).

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.posts_mentioning`.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field, field_validator

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


class AuthorsConnectedByTopicIn(BaseModel):
    """Input parameters for the ``authors_connected_by_topic`` tool.

    Attributes:
        seed_author: Seed author handle or display name, case-insensitive.
        min_overlap: Minimum number of shared topics required to count as
            connected (>= 1).
        max_hops: Traversal depth. v1 supports 1 only; larger values are
            rejected at validation time.
        limit: Maximum connected authors returned per matched seed, in
            [1, 500].
    """

    seed_author: str
    min_overlap: int = Field(default=1, ge=1)
    max_hops: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}

    @field_validator("max_hops")
    @classmethod
    def _max_hops_supported(cls, value: int) -> int:
        """Reject ``max_hops`` greater than 1 (multi-hop is not yet supported).

        Args:
            value: Proposed ``max_hops`` value.

        Returns:
            ``value`` unchanged when supported.

        Raises:
            ValueError: When ``value`` exceeds 1.
        """
        if value > 1:
            raise ValueError("max_hops > 1 not yet supported")
        return value


class AuthorRef(BaseModel):
    """A lightweight reference to an author node.

    Attributes:
        author_id: Canonical ``:Author.id``.
        handle: Author handle, if known.
        display_name: Author display name, if known.
    """

    author_id: str
    handle: str | None
    display_name: str | None


class ConnectedAuthor(BaseModel):
    """An author connected to the seed by shared topics.

    Attributes:
        author_id: Canonical ``:Author.id``.
        handle: Author handle, if known.
        display_name: Author display name, if known.
        overlap: Number of distinct topics shared with the seed.
        shared_topics: Display names of the shared topics (resolved
            entity canonical names or alias surface forms).
    """

    author_id: str
    handle: str | None
    display_name: str | None
    overlap: int
    shared_topics: list[str]


class SeedConnections(BaseModel):
    """Connections found for a single matched seed author.

    Attributes:
        seed: The matched seed author.
        connected: Authors connected to the seed, descending by overlap.
    """

    seed: AuthorRef
    connected: list[ConnectedAuthor]


class AuthorsConnectedByTopicOut(BaseModel):
    """Output of the ``authors_connected_by_topic`` tool.

    Attributes:
        results: One group per matched seed author. Empty when the seed
            name matches no author.
    """

    results: list[SeedConnections]

    def audit_entities(self) -> list[str]:
        """Return resolved entity ids touched by this result set.

        Returns:
            An empty list in v1: shared topics are alias surface forms
            (no canonical id) until entity resolution lands. Revisit to
            surface entity ids once resolution writes ``:Entity`` nodes.
        """
        return []

    def audit_result_count(self) -> int:
        """Return the total number of connected authors across all seeds.

        Returns:
            Sum of each seed group's connected-author count.
        """
        return sum(len(group.connected) for group in self.results)


@register_tool(
    name="authors_connected_by_topic",
    input_model=AuthorsConnectedByTopicIn,
    output_model=AuthorsConnectedByTopicOut,
)
@audited
def authors_connected_by_topic(
    driver: Driver,
    params: AuthorsConnectedByTopicIn,
    *,
    user: str,
    audit: AuditLogger,
) -> AuthorsConnectedByTopicOut:
    """Return authors sharing topics with ``params.seed_author``.

    Loads the ``authors_connected_by_topic.cypher`` template and runs it.
    The ``@audited`` decorator records the invocation; this function only
    owns query execution and result shaping.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        An :class:`AuthorsConnectedByTopicOut` with one group per matched
        seed author.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("authors_connected_by_topic")
    cypher_params: dict[str, Any] = {
        "seed_author": params.seed_author,
        "min_overlap": params.min_overlap,
        "limit": params.limit,
    }
    results: list[SeedConnections] = []
    with driver.session() as session:
        result = session.run(cypher, **cypher_params)
        for row in result:
            connected = [
                ConnectedAuthor(
                    author_id=c["author_id"],
                    handle=c["handle"],
                    display_name=c["display_name"],
                    overlap=c["overlap"],
                    shared_topics=list(c["shared_topics"]),
                )
                for c in row["connected"]
            ]
            results.append(
                SeedConnections(
                    seed=AuthorRef(
                        author_id=row["seed_author_id"],
                        handle=row["seed_handle"],
                        display_name=row["seed_display_name"],
                    ),
                    connected=connected,
                )
            )
    return AuthorsConnectedByTopicOut(results=results)
