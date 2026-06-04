"""Tool: topic_co_occurrence(topic, from, to, limit).

Given a seed topic, returns the other topics mentioned in the same posts,
ranked by the number of shared posts (1-hop, same-post co-occurrence). Topic
identity follows the coalesce(entity, alias) rule used across the graph tools:
a topic is the resolved ``:Entity`` when present, else the ``:Alias`` surface
form. The seed string is resolved to its entity identity first, so seeding by
any one of an entity's surface forms (or its canonical name) spans every post
mentioning that entity, and the seed is excluded from its own list by identity
rather than by display name.

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.posts_mentioning`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


class TopicCoOccurrenceIn(BaseModel):
    """Input parameters for the ``topic_co_occurrence`` tool.

    Attributes:
        topic: Seed topic — an alias surface form or entity canonical
            name. Matching is case-insensitive.
        from_: Inclusive lower bound on post timestamp. ``None`` means no
            lower bound. Exposed as ``from`` on the wire.
        to: Exclusive upper bound on post timestamp. ``None`` means no
            upper bound.
        limit: Maximum number of co-occurring topics to return, in
            [1, 500].
    """

    topic: str
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}


class CooccurringTopic(BaseModel):
    """A topic co-mentioned with the seed and its shared-post count.

    Attributes:
        topic: Resolved entity canonical name when available, otherwise
            the alias surface form.
        entity_id: Canonical entity id when resolved, otherwise ``None``.
        count: Number of posts mentioning both this topic and the seed.
    """

    topic: str
    entity_id: str | None
    count: int


class TopicCoOccurrenceOut(BaseModel):
    """Output of the ``topic_co_occurrence`` tool.

    Attributes:
        seed: Echo of the requested seed topic.
        cooccurring: Co-occurring topics in descending count order.
    """

    seed: str
    cooccurring: list[CooccurringTopic]

    def audit_entities(self) -> list[str]:
        """Return distinct resolved entity ids among the co-occurring topics.

        Returns:
            Entity ids in first-seen order, deduped. Unresolved alias
            topics are omitted because they have no canonical id.
        """
        seen: list[str] = []
        for topic in self.cooccurring:
            if topic.entity_id and topic.entity_id not in seen:
                seen.append(topic.entity_id)
        return seen

    def audit_result_count(self) -> int:
        """Return the number of co-occurring topics in this result set.

        Returns:
            Length of :attr:`cooccurring`.
        """
        return len(self.cooccurring)


@register_tool(
    name="topic_co_occurrence",
    input_model=TopicCoOccurrenceIn,
    output_model=TopicCoOccurrenceOut,
)
@audited
def topic_co_occurrence(
    driver: Driver,
    params: TopicCoOccurrenceIn,
    *,
    user: str,
    audit: AuditLogger,
) -> TopicCoOccurrenceOut:
    """Return topics co-mentioned with ``params.topic`` in the same posts.

    Loads the ``topic_co_occurrence.cypher`` template and runs it. The
    ``@audited`` decorator records the invocation; this function only owns
    query execution and result shaping.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        A :class:`TopicCoOccurrenceOut` with at most ``params.limit``
        co-occurring topics.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("topic_co_occurrence")
    cypher_params: dict[str, Any] = {
        "topic": params.topic,
        "from": params.from_.isoformat() if params.from_ else None,
        "to": params.to.isoformat() if params.to else None,
        "limit": params.limit,
    }
    cooccurring: list[CooccurringTopic] = []
    with driver.session() as session:
        result = session.run(cypher, **cypher_params)
        cooccurring = [
            CooccurringTopic(
                topic=row["topic"],
                entity_id=row["entity_id"],
                count=row["count"],
            )
            for row in result
        ]
    return TopicCoOccurrenceOut(seed=params.topic, cooccurring=cooccurring)
