"""Entity resolution pipeline.

The per-alias pipeline is:

    normalize_surface
      → lookup_resolved_norm_key    (durable cross-run dedup by normalized key)
      → cluster_candidates          (vector index on Entity.embedding)
      → llm_tiebreaker              (only when cluster has > 1 candidate)
      → mint_entity                 (when no confident match)

Each step is a separate function so it can be unit-tested and so the
thresholds in `ResolutionConfig` can be tuned without rewriting the
plumbing. `resolve_alias_to_entity` runs the pipeline for one alias and
writes the `:RESOLVED_TO` edge; `resolve_all` is the batch, re-runnable
entry point (exposed via `python -m chorus.ingestion.cli resolve`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger
from neo4j import Driver

from chorus.audit.logger import AuditLogger
from chorus.inference import provider
from chorus.utils.env_cfg import ResolutionConfig, load_inference_env


def normalize_surface(s: str, cfg: ResolutionConfig) -> str:
    """Normalize a surface form for alias comparison.

    Strips surrounding whitespace and, when configured, casefolds for
    case-insensitive comparison. This is **not** the ``:Alias.surface_form``
    stored in the graph — extraction stores the raw NER text there. The
    normalized value is the in-run clustering key in :func:`resolve_all` and
    is persisted as ``:Alias.norm_key`` when an alias is resolved, so
    case/whitespace variants cluster across runs too (paired with the label).

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


def lookup_resolved_norm_key(driver: Driver, norm_key: str, label: str | None) -> str | None:
    """Return the entity an already-resolved alias with this (norm_key, label) points to.

    This is the durable cross-run dedup: a case/whitespace variant ingested in
    a later run (``berlin`` after ``Berlin``) finds the entity an earlier
    variant minted, even when their embeddings would not vector-match. The
    label predicate is null-safe and mirrors :func:`cluster_candidates`, so a
    PERSON ``Apple`` never collapses into a FOOD ``apple``. Backed by the
    ``alias_norm_key`` index (migration 004).

    Args:
        driver: Open Neo4j driver.
        norm_key: Normalized surface form (see :func:`normalize_surface`).
        label: Alias label (GLiNER type), or ``None`` to match only untyped
            resolved aliases.

    Returns:
        The matching entity id, or ``None`` if no resolved alias shares this
        normalized key and label. ``ORDER BY e.id`` keeps the choice
        deterministic if legacy data exposes more than one.
    """
    cypher = """
    MATCH (a:Alias)-[:RESOLVED_TO]->(e:Entity)
    WHERE a.norm_key = $norm_key
      AND (a.label = $label OR ($label IS NULL AND a.label IS NULL))
    RETURN e.id AS id
    ORDER BY e.id LIMIT 1
    """
    with driver.session() as s:
        record = s.run(cypher, norm_key=norm_key, label=label).single()
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
    ``score >= threshold`` and a null-safe ``type == entity_type``, returning
    the top ``k`` as ``{id, canonical_name, type, score}`` descending by score.

    Type matching is symmetric: a typed query matches only entities of that
    type, and an untyped query (``entity_type=None``) matches only untyped
    entities — an untyped alias never cross-matches into a typed entity.

    Args:
        driver: Open Neo4j driver.
        embedding: Query embedding vector with ``EMBED_DIM`` dimensions.
        threshold: Minimum cosine similarity for an entity to count as
            a candidate.
        k: Maximum number of candidates to return.
        entity_type: Required entity type; ``None`` matches only untyped
            entities.

    Returns:
        Candidate dicts ``{id, canonical_name, type, score}``, descending by
        score, at most ``k`` of them. Empty when nothing clears the threshold.
    """
    fetch = max(4 * k, 20)
    cypher = """
    CALL db.index.vector.queryNodes('entity_embedding', $fetch, $embedding)
      YIELD node, score
    WHERE score >= $threshold
      AND (
        ($entity_type IS NULL AND node.type IS NULL)
        OR node.type = $entity_type
      )
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
    embed_model: str = "",
    norm_key: str,
) -> str:
    """Mint a new :Entity for an alias and link it, atomically; return its id.

    The entity CREATE and the ``:RESOLVED_TO`` edge are written in a single
    statement that first MATCHes the alias, so they commit together: a crash
    can never leave an orphan :Entity with no incoming edge, and a missing
    alias creates nothing.

    Args:
        driver: Open Neo4j driver.
        surface: Surface form of the (already-existing) :Alias; also the
            new entity's canonical name.
        embedding: Name embedding stored on the entity for future matching.
        entity_type: Entity type (from the alias label), or ``None``.
        embed_model: Embedding model id, recorded on the edge for provenance.
        norm_key: Normalized surface form, stamped on the alias for durable
            cross-run dedup.

    Returns:
        The minted entity's id (a fresh UUID4 string).

    Raises:
        RuntimeError: If no :Alias node exists for ``surface`` (so nothing
            is created — no orphan entity).
    """
    entity_id = str(uuid.uuid4())
    cypher = """
    MATCH (a:Alias {surface_form: $surface})
    CREATE (e:Entity {
        id: $id, canonical_name: $surface, type: $entity_type,
        embedding: $embedding, description: null
    })
    CREATE (a)-[r:RESOLVED_TO]->(e)
    SET r.method = 'minted', r.score = null,
        r.embed_model = $embed_model, r.resolved_at = datetime(),
        a.norm_key = $norm_key
    RETURN e.id AS id
    """
    with driver.session() as session:
        record = session.run(
            cypher,
            id=entity_id,
            surface=surface,
            entity_type=entity_type,
            embedding=embedding,
            embed_model=embed_model,
            norm_key=norm_key,
        ).single()
    if record is None:
        raise RuntimeError(f"cannot mint entity: no :Alias node for surface_form={surface!r}")
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
    # Match by exact id token, not substring: otherwise an id like "e-1"
    # would be matched inside a reply naming "e-12". Tokenize on runs of
    # id-legal characters (alphanumerics, hyphen, underscore) so UUIDs and
    # hyphenated ids survive while surrounding prose is split away.
    tokens = set(re.findall(r"[\w-]+", text))
    matched = [c["id"] for c in candidates if c["id"] in tokens]
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
    :func:`lookup_resolved_norm_key` (durable cross-run dedup) →
    :func:`cluster_candidates` → :func:`llm_tiebreaker` → mint a new entity
    if nothing returns a confident match — then writes the ``:RESOLVED_TO``
    edge.

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
        was decided: ``cross_run`` (matched an already-resolved variant by
        normalized key), ``minted``, ``vector_single``, ``vector_llm``, or
        ``vector_topk``.
    """
    norm_key = normalize_surface(surface, cfg)
    existing = lookup_resolved_norm_key(driver, norm_key, entity_type)
    if existing is not None:
        # A prior run (or this alias itself) already resolved this normalized
        # surface+label; re-attach idempotently. The cardinality guard in
        # _write_resolved_to keeps the alias at a single edge.
        _write_resolved_to(
            driver, surface, existing, method="cross_run", score=None, embed_model=embed_model, norm_key=norm_key
        )
        return existing, "cross_run"

    candidates = cluster_candidates(
        driver, embedding, cfg.embed_cluster_threshold, k=cfg.vector_k, entity_type=entity_type
    )
    # Mint paths call mint_entity, which writes the entity AND its RESOLVED_TO
    # edge atomically and returns; attach paths fall through to _write_resolved_to.
    if not candidates:
        return (
            mint_entity(
                driver, surface, embedding, entity_type=entity_type, embed_model=embed_model, norm_key=norm_key
            ),
            "minted",
        )

    if len(candidates) == 1:
        entity_id, method, score = candidates[0]["id"], "vector_single", candidates[0]["score"]
    elif cfg.llm_tiebreak_enabled:
        # Multiple candidates clear the threshold: ask the LLM to disambiguate.
        # If it affirms one, attach to it; if it judges that none match (or its
        # reply is unparseable), trust that judgement and mint a new entity
        # rather than force-merge into the top score.
        chosen = llm_tiebreaker(surface, candidates)
        if chosen is None:
            return (
                mint_entity(
                    driver, surface, embedding, entity_type=entity_type, embed_model=embed_model, norm_key=norm_key
                ),
                "minted",
            )
        entity_id, method = chosen, "vector_llm"
        score = next((c["score"] for c in candidates if c["id"] == chosen), None)
    else:
        # Tie-break disabled: the LLM is never consulted, so we must not mint on
        # ambiguity (that would fragment). Attach to the top-scoring candidate.
        entity_id, method, score = candidates[0]["id"], "vector_topk", candidates[0]["score"]

    _write_resolved_to(
        driver, surface, entity_id, method=method, score=score, embed_model=embed_model, norm_key=norm_key
    )
    return entity_id, method


def _write_resolved_to(
    driver: Driver,
    surface: str,
    entity_id: str,
    *,
    method: str,
    score: float | None,
    embed_model: str,
    norm_key: str,
) -> None:
    """MERGE the :RESOLVED_TO edge from an alias to an entity with provenance.

    Enforces one outgoing ``:RESOLVED_TO`` edge per alias (last-writer-wins):
    any existing edge to a *different* entity is dropped before the new one is
    merged, so a re-run that picks a different entity corrects the link rather
    than leaving two. Re-resolution to the *same* entity deletes nothing and
    the MERGE stays idempotent. Neo4j CE has no relationship-cardinality
    constraint, so this app-level guard is the enforcement; under concurrent
    writers a transient second edge is still possible and is collapsed on the
    next run (see ADR 0012). The alias's ``norm_key`` is stamped in the same
    statement, so an alias can never carry a ``:RESOLVED_TO`` edge without its
    durable key.

    Args:
        driver: Open Neo4j driver.
        surface: Surface form of the (already-existing) :Alias node.
        entity_id: Target :Entity id.
        method: How the resolution was decided (recorded on the edge).
        score: Vector similarity score, when applicable.
        embed_model: Embedding model id used, for provenance.
        norm_key: Normalized surface form, stamped on the alias for durable
            cross-run dedup.
    """
    cypher = """
    MATCH (a:Alias {surface_form: $surface})
    MATCH (e:Entity {id: $entity_id})
    OPTIONAL MATCH (a)-[old:RESOLVED_TO]->(other:Entity)
    WHERE other.id <> $entity_id
    DELETE old
    MERGE (a)-[r:RESOLVED_TO]->(e)
    SET r.method = $method, r.score = $score,
        r.embed_model = $embed_model, r.resolved_at = datetime(),
        a.norm_key = $norm_key
    """
    with driver.session() as session:
        session.run(
            cypher,
            surface=surface,
            entity_id=entity_id,
            method=method,
            score=score,
            embed_model=embed_model,
            norm_key=norm_key,
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
        attached_cross_run: Resolved to an entity an earlier run had already
            linked, matched by durable normalized key (see issue #24).
        minted: New entities created.
        skipped: Retained for compatibility; superseded by
            ``attached_cross_run`` and no longer emitted.
    """

    processed: int = 0
    attached_single: int = 0
    attached_llm: int = 0
    attached_topk: int = 0
    attached_cache: int = 0
    attached_cross_run: int = 0
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
    "cross_run": "attached_cross_run",
    "minted": "minted",
    "skipped": "skipped",
}


def _embed_in_chunks(surfaces: list[str], *, chunk: int = 128) -> list[list[float]]:
    """Embed surface forms in batches to bound request size."""
    out: list[list[float]] = []
    for i in range(0, len(surfaces), chunk):
        out.extend(provider.embed(surfaces[i : i + chunk]))
    return out


def resolve_all(driver: Driver, cfg: ResolutionConfig, audit: AuditLogger, *, user: str) -> ResolutionSummary:
    """Resolve every unresolved :Alias to a canonical :Entity (batch, idempotent).

    Aliases are processed most-mentioned first, so the common surface form
    mints and becomes the canonical name. An in-run normalized cache makes
    case-variant aliases cluster deterministically despite vector-index lag.
    The whole run is recorded as one §76 audit row (``tool_name="resolve_all"``)
    via ``audit``; an empty run writes no row.

    Args:
        driver: Open Neo4j driver.
        cfg: Resolution thresholds/toggles.
        audit: §76 audit logger; one row is written per non-empty run, with
            the run config as params, the minted/attached entity ids as
            ``entities_touched``, and the aliases processed as the result
            count. A failed run is recorded with ``status="error"``.
        user: Authenticated principal attributed on the audit row.

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
    params = {
        "embed_cluster_threshold": cfg.embed_cluster_threshold,
        "llm_tiebreak_enabled": cfg.llm_tiebreak_enabled,
        "case_normalize": cfg.case_normalize,
        "vector_k": cfg.vector_k,
        "embed_model": embed_model,
    }
    with audit.time_tool(user, "resolve_all", params) as slot:
        vectors = _embed_in_chunks([a[0] for a in aliases])
        # Fail before any write if the provider returned a different number of
        # embeddings than surfaces, so we never leave the graph half-resolved.
        if len(vectors) != len(aliases):
            raise ValueError(
                f"embed returned {len(vectors)} vectors for {len(aliases)} aliases; "
                "aborting resolution before any write"
            )

        counts = dict.fromkeys(_METHOD_FIELD.values(), 0)
        counts["processed"] = 0
        # Cache key is (normalized surface, label): two case/whitespace variants
        # cluster only when they share a type, so a PERSON "Apple" never collapses
        # into a FOOD "apple". Mirrors cluster_candidates' same-type filter.
        run_cache: dict[tuple[str, str | None], str] = {}
        touched: set[str] = set()

        for (surface, label), vec in zip(aliases, vectors, strict=True):
            norm_key = normalize_surface(surface, cfg)
            cache_key = (norm_key, label)
            if cache_key in run_cache:
                entity_id = run_cache[cache_key]
                _write_resolved_to(
                    driver,
                    surface,
                    entity_id,
                    method="run_cache",
                    score=None,
                    embed_model=embed_model,
                    norm_key=norm_key,
                )
                counts["attached_cache"] += 1
            else:
                entity_id, method = resolve_alias_to_entity(
                    driver, surface, vec, cfg, entity_type=label, embed_model=embed_model
                )
                run_cache[cache_key] = entity_id
                counts[_METHOD_FIELD[method]] += 1
            touched.add(entity_id)
            counts["processed"] += 1

        slot.entities_touched = sorted(touched)
        slot.result_count = counts["processed"]
        logger.info("resolution complete: {}", counts)
        return ResolutionSummary(**counts)


def backfill_norm_keys(driver: Driver, cfg: ResolutionConfig, *, batch: int = 500) -> int:
    """Stamp ``norm_key`` on resolved aliases that predate the durable-key change.

    Cross-run dedup matches on ``(norm_key, label)``, so aliases resolved
    before ``norm_key`` existed must be backfilled — otherwise a new variant
    could mint a duplicate before the old alias is re-touched. The key is
    computed with the same Python :func:`normalize_surface` as live resolution
    (``str.casefold()``, which differs from Cypher ``toLower()`` for non-ASCII
    such as German ``ß`` → ``ss``), so a Cypher backfill is deliberately
    avoided. Idempotent: only resolved aliases with a missing ``norm_key`` are
    touched, so re-running is a no-op. See ADR 0012.

    Args:
        driver: Open Neo4j driver.
        cfg: Resolution configuration (controls case-normalization).
        batch: UNWIND write-back chunk size, mirroring the connections writer.

    Returns:
        Number of aliases stamped this run.
    """
    fetch = """
    MATCH (a:Alias)-[:RESOLVED_TO]->(:Entity)
    WHERE a.norm_key IS NULL
    RETURN a.surface_form AS surface_form
    """
    with driver.session() as session:
        surfaces = [r["surface_form"] for r in session.run(fetch)]
    if not surfaces:
        return 0

    rows = [{"surface_form": sf, "norm_key": normalize_surface(sf, cfg)} for sf in surfaces]
    write = """
    UNWIND $rows AS row
    MATCH (a:Alias {surface_form: row.surface_form})
    SET a.norm_key = row.norm_key
    """
    with driver.session() as session:
        for i in range(0, len(rows), batch):
            session.run(write, rows=rows[i : i + batch]).consume()
    logger.info("norm_key backfill stamped {} aliases", len(rows))
    return len(rows)
