"""GET /stats — graph-diagnostics aggregate.

One authenticated, §76-audited round-trip to Neo4j that returns node
counts, edge counts, named highlights, and a platform breakdown.  The
query is never empty-graph-fatal: each CALL{} subquery returns 0/[]/null
independently, so a freshly-initialised database yields a well-formed
response rather than an error.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from chorus.api.auth.principal import resolve_principal
from chorus.audit.logger import AuditLogger, AuditRecord
from chorus.tools._template_loader import load_template

router = APIRouter(tags=["stats"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class NodeCounts(BaseModel):
    """Counts of every significant node label in the graph.

    Attributes:
        posts: Total :Post nodes (all subtypes).
        authors: Total :Author nodes.
        entities: Total :Entity nodes.
        hashtags: Total :Hashtag nodes.
        groups: Total :Group nodes.
        platforms: Total :Platform nodes.
        aliases: Total :Alias nodes.
    """

    posts: int
    authors: int
    entities: int
    hashtags: int
    groups: int
    platforms: int
    aliases: int


class EdgeCounts(BaseModel):
    """Counts of load-bearing relationship types.

    Attributes:
        mentions: Total :MENTIONS edges.
        authored: Total :AUTHORED edges.
        follows: Total :FOLLOWS edges.
        friends: Total :FRIENDS_WITH edges.
        resolved: Total :RESOLVED_TO edges.
    """

    mentions: int
    authored: int
    follows: int
    friends: int
    resolved: int


class TopEntityItem(BaseModel):
    """One entry in the top-entities highlight list.

    Attributes:
        name: Canonical entity name when resolved, else the alias surface
            form.
        count: Number of :Post nodes that mention this name.
    """

    name: str
    count: int


class TopAuthorItem(BaseModel):
    """One entry in the top-authors highlight list.

    Attributes:
        author_id: The :Author.id property.
        label: Best available display string (display_name → handle → id).
        count: Number of :Post nodes the author authored.
    """

    author_id: str
    label: str
    count: int


class PlatformCount(BaseModel):
    """One row of the per-platform post count.

    Attributes:
        platform: :Platform.name.
        count: Number of :Post nodes on that platform.
    """

    platform: str
    count: int


class ResolutionCoverage(BaseModel):
    """Alias-resolution coverage figures.

    Attributes:
        resolved_aliases: Aliases that have at least one :RESOLVED_TO edge.
        total_aliases: Total :Alias nodes.
    """

    resolved_aliases: int
    total_aliases: int


class StatsOut(BaseModel):
    """Full graph-diagnostics snapshot returned by GET /stats.

    Attributes:
        counts: Node counts by label.
        edges: Relationship counts by type.
        top_entities: Up to 5 most-mentioned entities/aliases.
        top_authors: Up to 5 most-prolific authors.
        posts_by_platform: Post count per platform.
        latest_ingested_at: ISO-8601 string of the most-recent
            Post.ingested_at, or None when the graph is empty.
        resolution: Alias-resolution coverage.
    """

    counts: NodeCounts
    edges: EdgeCounts
    top_entities: list[TopEntityItem]
    top_authors: list[TopAuthorItem]
    posts_by_platform: list[PlatformCount]
    latest_ingested_at: str | None
    resolution: ResolutionCoverage


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=StatsOut)
def get_stats(
    request: Request,
    principal: str = Depends(resolve_principal),
) -> StatsOut:
    """Return a graph-diagnostics snapshot for the authenticated principal.

    Runs a single parameterless Cypher statement that uses independent
    ``CALL {}`` subqueries — each isolated so that an empty graph returns
    zeros rather than raising.  Writes one §76 BDSG audit row (principal +
    ``"stats"`` action) via the shared :class:`~chorus.audit.logger.AuditLogger`
    on ``app.state``.

    Args:
        request: The active FastAPI request (provides driver + audit logger
            from ``app.state``).
        principal: Authenticated identity resolved from the trusted-header
            or ``CHORUS_DEFAULT_IDENTITY`` fallback.

    Returns:
        A populated :class:`StatsOut`; never raises on an empty graph.
    """
    driver = request.app.state.driver
    audit: AuditLogger = request.app.state.audit

    cypher = load_template("stats")

    with driver.session() as session:
        record = session.run(cypher).single()

    row: dict[str, Any] = dict(record) if record is not None else {}

    def _int(key: str) -> int:
        v = row.get(key)
        return int(v) if v is not None else 0

    def _list(key: str) -> list[Any]:
        v = row.get(key)
        return list(v) if v is not None else []

    top_entities = [
        TopEntityItem(name=item["name"], count=int(item["count"]))
        for item in _list("top_entities")
        if item.get("name") is not None
    ]
    top_authors = [
        TopAuthorItem(
            author_id=item["author_id"],
            label=item["label"],
            count=int(item["count"]),
        )
        for item in _list("top_authors")
        if item.get("author_id") is not None
    ]
    posts_by_platform = [
        PlatformCount(platform=item["platform"], count=int(item["count"]))
        for item in _list("posts_by_platform")
        if item.get("platform") is not None
    ]

    # Convert Neo4j DateTime → ISO string if necessary; None when absent.
    raw_lat = row.get("latest_ingested_at")
    if raw_lat is None:
        latest_ingested_at: str | None = None
    elif hasattr(raw_lat, "iso_format"):
        latest_ingested_at = raw_lat.iso_format()
    else:
        latest_ingested_at = str(raw_lat)

    out = StatsOut(
        counts=NodeCounts(
            posts=_int("post_count"),
            authors=_int("author_count"),
            entities=_int("entity_count"),
            hashtags=_int("hashtag_count"),
            groups=_int("group_count"),
            platforms=_int("platform_count"),
            aliases=_int("alias_count"),
        ),
        edges=EdgeCounts(
            mentions=_int("mentions_count"),
            authored=_int("authored_count"),
            follows=_int("follows_count"),
            friends=_int("friends_count"),
            resolved=_int("resolved_count"),
        ),
        top_entities=top_entities,
        top_authors=top_authors,
        posts_by_platform=posts_by_platform,
        latest_ingested_at=latest_ingested_at,
        resolution=ResolutionCoverage(
            resolved_aliases=_int("resolved_aliases"),
            total_aliases=_int("total_aliases"),
        ),
    )

    # §76 BDSG audit entry — lightweight: principal + action, no entities or
    # result counts (this is an aggregate view, not a targeted lookup).
    audit.record(
        AuditRecord(
            user=principal,
            tool_name="stats",
            params={"action": "diagnostics"},
            entities_touched=[],
            result_count=0,
            status="ok",
        )
    )

    return out
