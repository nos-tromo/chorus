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
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger
from neo4j import Driver

from chorus.inference import provider
from chorus.utils.env_cfg import ResolutionConfig, load_inference_env


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
    entity_type: str | None = None,
) -> list[dict[str, Any]]:
    """Find candidate entities by vector similarity, filtered to one type.

    Over-fetches from the ``entity_embedding`` vector index, then filters to
    ``score >= threshold`` and (when given) ``type == entity_type``, returning
    the top ``k`` as ``{id, canonical_name, type, score}`` descending by score.

    Args:
        driver: Open Neo4j driver.
        embedding: Query embedding vector with ``EMBED_DIM`` dimensions.
        threshold: Minimum cosine similarity for an entity to count as
            a candidate.
        k: Maximum number of candidates to return.
        entity_type: When set, only entities of this type are returned.

    Returns:
        Candidate dicts ``{id, canonical_name, type, score}``, descending by
        score, at most ``k`` of them. Empty when nothing clears the threshold.
    """
    fetch = max(4 * k, 20)
    cypher = """
    CALL db.index.vector.queryNodes('entity_embedding', $fetch, $embedding)
      YIELD node, score
    WHERE score >= $threshold
      AND ($entity_type IS NULL OR node.type = $entity_type)
    RETURN node.id AS id, node.canonical_name AS canonical_name,
           node.type AS type, score
    ORDER BY score DESC
    LIMIT $k
    """
    with driver.session() as session:
        rows = session.run(
            cypher,
            fetch=fetch,
            embedding=embedding,
            threshold=threshold,
            entity_type=entity_type,
            k=k,
        ).data()
    return [
        {
            "id": r["id"],
            "canonical_name": r["canonical_name"],
            "type": r["type"],
            "score": r["score"],
        }
        for r in rows
    ]


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


@dataclass(frozen=True)
class ResolutionSummary:
    """Counts from a :func:`resolve_all` run.

    Attributes:
        processed: Total aliases processed this run.
        attached_single: Resolved to a single vector candidate.
        attached_llm: Resolved via the LLM tie-breaker.
        attached_topk: Resolved to the top-score candidate (LLM abstained).
        attached_cache: Resolved via the in-run normalized cache.
        minted: New entities created.
        skipped: Already resolved (defensive; resolve_all only fetches
            unresolved aliases).
    """

    processed: int = 0
    attached_single: int = 0
    attached_llm: int = 0
    attached_topk: int = 0
    attached_cache: int = 0
    minted: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return the summary as a plain dict (field → count)."""
        return asdict(self)


_METHOD_FIELD = {
    "vector_single": "attached_single",
    "vector_llm": "attached_llm",
    "vector_topk": "attached_topk",
    "run_cache": "attached_cache",
    "minted": "minted",
    "skipped": "skipped",
}


def _embed_in_chunks(surfaces: list[str], *, chunk: int = 128) -> list[list[float]]:
    """Embed surface forms in batches to bound request size."""
    out: list[list[float]] = []
    for i in range(0, len(surfaces), chunk):
        out.extend(provider.embed(surfaces[i : i + chunk]))
    return out


def resolve_all(driver: Driver, cfg: ResolutionConfig) -> ResolutionSummary:
    """Resolve every unresolved :Alias to a canonical :Entity (batch, idempotent).

    Aliases are processed most-mentioned first, so the common surface form
    mints and becomes the canonical name. An in-run normalized cache makes
    case-variant aliases cluster deterministically despite vector-index lag.

    Args:
        driver: Open Neo4j driver.
        cfg: Resolution thresholds/toggles.

    Returns:
        A :class:`ResolutionSummary` of per-method counts.
    """
    fetch = """
    MATCH (a:Alias) WHERE NOT (a)-[:RESOLVED_TO]->(:Entity)
    OPTIONAL MATCH (a)<-[:MENTIONS]-(p:Post)
    WITH a, count(p) AS mentions
    ORDER BY mentions DESC, a.surface_form ASC
    RETURN a.surface_form AS surface_form, a.label AS label
    """
    with driver.session() as session:
        aliases = [(r["surface_form"], r["label"]) for r in session.run(fetch)]
    if not aliases:
        return ResolutionSummary()

    embed_model = load_inference_env().embed_model
    vectors = _embed_in_chunks([a[0] for a in aliases])

    counts = dict.fromkeys(_METHOD_FIELD.values(), 0)
    counts["processed"] = 0
    run_cache: dict[str, str] = {}

    for (surface, label), vec in zip(aliases, vectors, strict=True):
        norm = normalize_surface(surface, cfg)
        if norm in run_cache:
            _write_resolved_to(
                driver,
                surface,
                run_cache[norm],
                method="run_cache",
                score=None,
                embed_model=embed_model,
            )
            counts["attached_cache"] += 1
        else:
            entity_id, method = resolve_alias_to_entity(
                driver, surface, vec, cfg, entity_type=label, embed_model=embed_model
            )
            run_cache[norm] = entity_id
            counts[_METHOD_FIELD[method]] += 1
        counts["processed"] += 1

    logger.info("resolution complete: {}", counts)
    return ResolutionSummary(**counts)
