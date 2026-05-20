"""Entity resolution pipeline (stubs).

The pipeline is:

    normalize_surface
      → lookup_alias                (cheap: exact match on Alias node)
      → cluster_candidates          (vector index on Entity.embedding)
      → llm_tiebreaker              (only when cluster has > 1 candidate)
      → mint_new_entity             (when no confident match)

Each step is a separate function so it can be unit-tested and so the
thresholds in `ResolutionConfig` can be tuned without rewriting the
plumbing. Bodies are deliberately left as `NotImplementedError` for v1;
they'll be filled in as part of the entity-resolution work tracked
under a separate ticket.
"""

from __future__ import annotations

from neo4j import Driver

from chorus.utils.env_cfg import ResolutionConfig


def normalize_surface(s: str, cfg: ResolutionConfig) -> str:
    out = s.strip()
    if cfg.case_normalize:
        out = out.casefold()
    return out


def lookup_alias(driver: Driver, surface: str) -> str | None:
    """Return the entity_id this surface form has been resolved to, if any."""
    cypher = """
    MATCH (a:Alias {surface_form: $surface})-[:RESOLVED_TO]->(e:Entity)
    RETURN e.id AS id LIMIT 1
    """
    with driver.session() as s:
        record = s.run(cypher, surface=surface).single()
    return record["id"] if record else None


def cluster_candidates(
    driver: Driver,
    embedding: list[float],
    threshold: float,
    *,
    k: int = 5,
) -> list[str]:
    """Vector-index lookup; returns entity ids whose cosine similarity is
    above `threshold`."""
    raise NotImplementedError("v1 resolution pending — see entity-resolution ticket")


def llm_tiebreaker(surface: str, candidates: list[dict]) -> str | None:
    """Call provider.chat with a small structured prompt; return the
    chosen entity_id or None to indicate 'no confident match — mint new'."""
    raise NotImplementedError("v1 resolution pending — see entity-resolution ticket")


def resolve_alias_to_entity(
    driver: Driver,
    surface: str,
    embedding: list[float],
    cfg: ResolutionConfig,
) -> str:
    """End-to-end resolution. Returns an entity_id (existing or new)."""
    raise NotImplementedError("v1 resolution pending — see entity-resolution ticket")
