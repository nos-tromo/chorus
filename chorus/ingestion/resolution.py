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

import uuid
from typing import Any

from neo4j import Driver

from chorus.utils.env_cfg import ResolutionConfig


def normalize_surface(s: str, cfg: ResolutionConfig) -> str:
    """Normalize a surface form for alias comparison.

    Strips surrounding whitespace and, when configured, casefolds for
    case-insensitive comparison. The output of this function is the
    canonical key used in the ``:Alias.surface_form`` property.

    Args:
        s: Raw surface form as extracted by NER.
        cfg: Resolution configuration controlling normalization toggles.

    Returns:
        Normalized surface form.
    """
    out = s.strip()
    if cfg.case_normalize:
        out = out.casefold()
    return out


def lookup_alias(driver: Driver, surface: str) -> str | None:
    """Return the entity id this surface form was previously resolved to.

    Args:
        driver: Open Neo4j driver.
        surface: Normalized surface form.

    Returns:
        The matching entity id, or ``None`` if this surface form has
        never been resolved.
    """
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
    """Find candidate entity ids by vector similarity (stub).

    Args:
        driver: Open Neo4j driver (unused until implemented).
        embedding: Query embedding vector with ``EMBED_DIM`` dimensions.
        threshold: Minimum cosine similarity for an entity to count as
            a candidate.
        k: Maximum number of candidates to return.

    Returns:
        Entity ids whose cosine similarity is above ``threshold``,
        sorted by descending similarity.

    Raises:
        NotImplementedError: Always; v1 resolution is pending.
    """
    raise NotImplementedError("v1 resolution pending — see entity-resolution ticket")


def mint_entity(
    driver: Driver,
    surface: str,
    embedding: list[float],
    *,
    entity_type: str | None = None,
) -> str:
    """Create a new :Entity from an unresolved alias and return its id.

    Args:
        driver: Open Neo4j driver.
        surface: Surface form to use as the canonical name.
        embedding: Name embedding stored on the entity for future matching.
        entity_type: Entity type (from the alias label), or ``None``.

    Returns:
        The minted entity's id (a fresh UUID4 string).
    """
    entity_id = str(uuid.uuid4())
    cypher = """
    CREATE (e:Entity {
        id: $id, canonical_name: $surface, type: $entity_type,
        embedding: $embedding, description: null
    })
    """
    with driver.session() as session:
        session.run(
            cypher,
            id=entity_id,
            surface=surface,
            entity_type=entity_type,
            embedding=embedding,
        ).consume()
    return entity_id


def llm_tiebreaker(surface: str, candidates: list[dict[str, Any]]) -> str | None:
    """Pick the candidate entity that matches ``surface`` via an LLM call.

    Args:
        surface: The unresolved surface form.
        candidates: Candidate entities with at least ``id``,
            ``canonical_name``, and ``type`` keys.

    Returns:
        The chosen entity id, or ``None`` to signal "no confident match"
        (the caller then falls back to the top-score candidate). The model
        response must contain exactly one known candidate id; otherwise the
        result is ``None``.
    """
    from chorus.inference import provider

    lines = [f"{i + 1}. id={c['id']} name={c['canonical_name']!r} type={c['type']}" for i, c in enumerate(candidates)]
    prompt = (
        "You are resolving an entity reference to a canonical entity.\n"
        f"Surface form: {surface!r}\n"
        "Candidates:\n" + "\n".join(lines) + "\n\n"
        "Reply with ONLY the id of the candidate that refers to the same "
        "real-world entity as the surface form, or NONE if none of them do."
    )
    text = provider.chat([{"role": "user", "content": prompt}]).strip()
    matched = [c["id"] for c in candidates if c["id"] in text]
    return matched[0] if len(matched) == 1 else None


def resolve_alias_to_entity(
    driver: Driver,
    surface: str,
    embedding: list[float],
    cfg: ResolutionConfig,
    *,
    entity_type: str | None = None,
    embed_model: str = "",
) -> tuple[str, str]:
    """End-to-end resolution from surface form to entity id.

    Runs the full pipeline:
    :func:`lookup_alias` (cache) → :func:`cluster_candidates` →
    :func:`llm_tiebreaker` → mint a new entity if nothing returns a
    confident match — then writes the ``:RESOLVED_TO`` edge.

    Args:
        driver: Open Neo4j driver.
        surface: Surface form to resolve (its :Alias node must exist).
        embedding: Embedding vector for the surface form, used during
            candidate clustering and stored on a minted entity.
        cfg: Resolution configuration.
        entity_type: Alias label; restricts matching to the same type and
            stamps a minted entity's ``type``.
        embed_model: Embedding model id, recorded on the edge for provenance.

    Returns:
        ``(entity_id, method)`` — the entity this surface maps to and how it
        was decided: ``skipped`` (already resolved), ``minted``,
        ``vector_single``, ``vector_llm``, or ``vector_topk``.
    """
    existing = lookup_alias(driver, surface)
    if existing is not None:
        return existing, "skipped"

    candidates = cluster_candidates(
        driver, embedding, cfg.embed_cluster_threshold, k=cfg.vector_k, entity_type=entity_type
    )
    score: float | None
    if not candidates:
        entity_id = mint_entity(driver, surface, embedding, entity_type=entity_type)
        method, score = "minted", None
    elif len(candidates) == 1:
        entity_id, method, score = candidates[0]["id"], "vector_single", candidates[0]["score"]
    else:
        chosen = llm_tiebreaker(surface, candidates) if cfg.llm_tiebreak_enabled else None
        if chosen is not None:
            entity_id, method = chosen, "vector_llm"
            score = next((c["score"] for c in candidates if c["id"] == chosen), None)
        else:
            entity_id, method, score = candidates[0]["id"], "vector_topk", candidates[0]["score"]

    _write_resolved_to(driver, surface, entity_id, method=method, score=score, embed_model=embed_model)
    return entity_id, method


def _write_resolved_to(
    driver: Driver,
    surface: str,
    entity_id: str,
    *,
    method: str,
    score: float | None,
    embed_model: str,
) -> None:
    """MERGE the :RESOLVED_TO edge from an alias to an entity with provenance.

    Args:
        driver: Open Neo4j driver.
        surface: Surface form of the (already-existing) :Alias node.
        entity_id: Target :Entity id.
        method: How the resolution was decided (recorded on the edge).
        score: Vector similarity score, when applicable.
        embed_model: Embedding model id used, for provenance.
    """
    cypher = """
    MATCH (a:Alias {surface_form: $surface})
    MATCH (e:Entity {id: $entity_id})
    MERGE (a)-[r:RESOLVED_TO]->(e)
    SET r.method = $method, r.score = $score,
        r.embed_model = $embed_model, r.resolved_at = datetime()
    """
    with driver.session() as session:
        session.run(
            cypher,
            surface=surface,
            entity_id=entity_id,
            method=method,
            score=score,
            embed_model=embed_model,
        ).consume()
