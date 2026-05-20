"""Reference tool: posts_mentioning(entity, from, to, limit).

Pattern: Pydantic input + Cypher template + `@audited` wrapper. Every
follow-on tool should look like this — single responsibility, no inline
Cypher, audit logging via the decorator.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from neo4j import Driver
from pydantic import BaseModel, Field

from chorus.audit.logger import AuditLogger
from chorus.tools._audit import audited, register_tool
from chorus.tools._template_loader import load_template


class PostsMentioningIn(BaseModel):
    entity: str
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)

    model_config = {"populate_by_name": True}


class PostsMentioningHit(BaseModel):
    uuid: str
    text: str
    ts: datetime
    labels: list[str]
    entity_id: str


class PostsMentioningOut(BaseModel):
    hits: list[PostsMentioningHit]

    def audit_entities(self) -> list[str]:
        seen: list[str] = []
        for h in self.hits:
            if h.entity_id not in seen:
                seen.append(h.entity_id)
        return seen

    def audit_result_count(self) -> int:
        return len(self.hits)


@register_tool(
    name="posts_mentioning",
    input_model=PostsMentioningIn,
    output_model=PostsMentioningOut,
)
@audited
def posts_mentioning(
    driver: Driver,
    params: PostsMentioningIn,
    *,
    user: str,
    audit: AuditLogger,
) -> PostsMentioningOut:
    del user, audit  # the @audited decorator owns the audit write
    cypher = load_template("posts_mentioning")
    cypher_params: dict[str, Any] = {
        "entity": params.entity,
        "from": params.from_.isoformat() if params.from_ else None,
        "to": params.to.isoformat() if params.to else None,
        "limit": params.limit,
    }
    with driver.session() as session:
        result = session.run(cypher, **cypher_params)
        hits = [
            PostsMentioningHit(
                uuid=row["uuid"],
                text=row["text"],
                ts=row["ts"].to_native()
                if hasattr(row["ts"], "to_native")
                else row["ts"],
                labels=list(row["labels"]),
                entity_id=row["entity_id"],
            )
            for row in result
        ]
    return PostsMentioningOut(hits=hits)
