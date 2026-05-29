"""Tool: author_activity_summary(author, from, to).

Aggregates one matched author's activity over their ``:AUTHORED`` posts in an
optional time window: counts by artifact type, first/last activity, engagement
totals (with the expected-vs-collected delta preserved), and the author's top
mentioned topics. A name may match several authors; each is returned as its own
summary so distinct people are never silently merged.

Pattern: Pydantic input + Cypher template + ``@audited`` wrapper, identical in
shape to :mod:`chorus.tools.posts_mentioning`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from neo4j import Driver
from pydantic import BaseModel, Field

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


class AuthorActivitySummaryIn(BaseModel):
    """Input parameters for the ``author_activity_summary`` tool.

    Attributes:
        author: Author handle or display name to match, case-insensitive.
        from_: Inclusive lower bound on post timestamp. ``None`` means no
            lower bound. Exposed as ``from`` on the wire.
        to: Exclusive upper bound on post timestamp. ``None`` means no
            upper bound.
    """

    author: str
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None

    model_config = {"populate_by_name": True}


class TopicCount(BaseModel):
    """A topic and how many of the author's posts mention it.

    Attributes:
        topic: Resolved entity canonical name when available, otherwise
            the alias surface form.
        entity_id: Canonical entity id when the mention is resolved,
            otherwise ``None``.
        count: Number of the author's posts that mention this topic.
    """

    topic: str
    entity_id: str | None
    count: int


class AuthorSummary(BaseModel):
    """Activity aggregates for a single matched author.

    Attributes:
        author_id: Canonical ``:Author.id``.
        handle: Author handle, if known.
        display_name: Author display name, if known.
        platform: Source platform, if known.
        post_count: Total authored posts in range.
        posting_count: Authored ``:Posting`` count in range.
        comment_count: Authored ``:Comment`` count in range.
        message_count: Authored ``:Message`` count in range.
        first_activity: Earliest post timestamp in range, or ``None``.
        last_activity: Latest post timestamp in range, or ``None``.
        expected_reactions_total: Sum of expected reactions over the
            author's postings (crawl-incompleteness signal; pair with
            ``collected_reactions_total``).
        collected_reactions_total: Sum of collected reactions.
        expected_comments_total: Sum of expected comments.
        collected_comments_total: Sum of collected comments.
        top_topics: Up to ten most-mentioned topics, descending by count.
    """

    author_id: str
    handle: str | None
    display_name: str | None
    platform: str | None
    post_count: int
    posting_count: int
    comment_count: int
    message_count: int
    first_activity: datetime | None
    last_activity: datetime | None
    expected_reactions_total: int
    collected_reactions_total: int
    expected_comments_total: int
    collected_comments_total: int
    top_topics: list[TopicCount]


class AuthorActivitySummaryOut(BaseModel):
    """Output of the ``author_activity_summary`` tool.

    Attributes:
        summaries: One summary per matched author. Empty when the name
            matches no author.
    """

    summaries: list[AuthorSummary]

    def audit_entities(self) -> list[str]:
        """Return distinct resolved entity ids surfaced in top topics.

        Returns:
            Entity ids in first-seen order, deduped. Unresolved alias
            topics are omitted because they have no canonical id.
        """
        seen: list[str] = []
        for su in self.summaries:
            for topic in su.top_topics:
                if topic.entity_id and topic.entity_id not in seen:
                    seen.append(topic.entity_id)
        return seen

    def audit_result_count(self) -> int:
        """Return the number of author summaries in this result set.

        Returns:
            Length of :attr:`summaries`.
        """
        return len(self.summaries)


@register_tool(
    name="author_activity_summary",
    input_model=AuthorActivitySummaryIn,
    output_model=AuthorActivitySummaryOut,
)
@audited
def author_activity_summary(
    driver: Driver,
    params: AuthorActivitySummaryIn,
    *,
    user: str,
    audit: AuditLogger,
) -> AuthorActivitySummaryOut:
    """Summarize each matched author's activity over an optional time range.

    Loads the ``author_activity_summary.cypher`` template and runs it. The
    ``@audited`` decorator records the invocation; this function only owns
    query execution and result shaping.

    Args:
        driver: Open Neo4j driver.
        params: Validated input parameters.
        user: Authenticated identity (consumed by ``@audited``).
        audit: Active audit logger (consumed by ``@audited``).

    Returns:
        An :class:`AuthorActivitySummaryOut` with one entry per matched
        author.
    """
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("author_activity_summary")
    cypher_params: dict[str, Any] = {
        "author": params.author,
        "from": params.from_.isoformat() if params.from_ else None,
        "to": params.to.isoformat() if params.to else None,
    }
    summaries: list[AuthorSummary] = []
    with driver.session() as session:
        result = session.run(cypher, **cypher_params)
        for row in result:
            summaries.append(
                AuthorSummary(
                    author_id=row["author_id"],
                    handle=row["handle"],
                    display_name=row["display_name"],
                    platform=row["platform"],
                    post_count=row["post_count"],
                    posting_count=row["posting_count"],
                    comment_count=row["comment_count"],
                    message_count=row["message_count"],
                    first_activity=_native(row["first_activity"]),
                    last_activity=_native(row["last_activity"]),
                    expected_reactions_total=row["expected_reactions_total"],
                    collected_reactions_total=row["collected_reactions_total"],
                    expected_comments_total=row["expected_comments_total"],
                    collected_comments_total=row["collected_comments_total"],
                    top_topics=[
                        TopicCount(
                            topic=topic["topic"],
                            entity_id=topic["entity_id"],
                            count=topic["count"],
                        )
                        for topic in row["top_topics"]
                    ],
                )
            )
    return AuthorActivitySummaryOut(summaries=summaries)
