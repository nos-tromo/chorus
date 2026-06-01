"""Entity extraction stage.

`extract_for_post(text, post_uuid, model_version)` calls the remote
GLiNER NER service and returns a list of dict-spans suitable for
`write_mentions`. The model version is recorded as a property on every
`MENTIONS` edge so re-extraction with a newer model can be audited and
rolled back.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from chorus.inference import ner_client


def extract_for_post(
    text: str,
    post_uuid: str,
    model_version: str,
    *,
    labels: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run NER over a single post and shape the spans for the graph writer.

    The ``model_version`` is recorded on every span and propagated to
    the ``:MENTIONS`` edge so re-extraction with a newer model can be
    audited and rolled back. It is **not** sent to the NER service: the
    ``/gliner`` endpoint serves a single configured model and chorus only
    records which model id it asked against, for provenance.

    Args:
        text: Body of the post to extract from.
        post_uuid: UUID of the source post, embedded in each span so
            the writer can link spans to the right node.
        model_version: NER model id stamped onto each returned span as
            ``model_version`` provenance metadata.
        labels: Optional whitelist of GLiNER labels. ``None`` uses the
            service's default label set.

    Returns:
        One dict per extracted span with keys ``surface_form``,
        ``label``, ``span_start``, ``span_end``, ``confidence``,
        ``post_uuid``, and ``model_version``.
    """
    spans = ner_client.extract_entities(text, labels=labels)
    return [
        {
            "surface_form": span.text,
            "label": span.label,
            "span_start": span.start,
            "span_end": span.end,
            "confidence": span.confidence,
            "post_uuid": post_uuid,
            "model_version": model_version,
        }
        for span in spans
    ]


def write_mentions(
    driver: Driver,
    post_uuid: str,
    spans: list[dict[str, Any]],
) -> int:
    """Write extracted spans as ``:MENTIONS`` edges with provenance.

    For each span, MERGEs the ``:Alias`` node by surface form — setting its
    GLiNER ``label`` on create and backfilling it on match when missing
    (``coalesce``; a label already set is never overwritten) — and MERGEs
    the ``:MENTIONS`` edge from the post to the alias.
    ``:RESOLVED_TO`` is left to :func:`resolution.resolve_alias_to_entity`
    — this function only ensures the alias and provenance exist.

    Args:
        driver: Open Neo4j driver.
        post_uuid: UUID of the post the spans were extracted from.
        spans: Spans as produced by :func:`extract_for_post`. Empty
            input is allowed and returns ``0``.

    Returns:
        Number of ``:MENTIONS`` edges visited by the query (created or
        already present); ``0`` for an empty span list.
    """
    if not spans:
        return 0
    cypher = """
    UNWIND $spans AS span
    MATCH (p:Post {uuid: $post_uuid})
    MERGE (al:Alias {surface_form: span.surface_form})
      ON CREATE SET al.label = span.label
      ON MATCH SET al.label = coalesce(al.label, span.label)
    MERGE (p)-[m:MENTIONS]->(al)
      ON CREATE SET
        m.span_start    = span.span_start,
        m.span_end      = span.span_end,
        m.confidence    = span.confidence,
        m.model_version = span.model_version
    RETURN count(m) AS n
    """
    with driver.session() as s:
        record = s.run(cypher, post_uuid=post_uuid, spans=spans).single()
    return int(record["n"]) if record else 0
