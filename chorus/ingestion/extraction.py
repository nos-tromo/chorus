"""Entity extraction stage.

`extract_for_post(text, post_uuid, model_version)` calls the inference
provider's NER endpoint and returns a list of dict-spans suitable for
`write_mentions`. The model version is recorded as a property on every
`MENTIONS` edge so re-extraction with a newer model can be audited and
rolled back.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from chorus.inference import provider


def extract_for_post(
    text: str,
    post_uuid: str,
    model_version: str,
    *,
    labels: list[str] | None = None,
) -> list[dict[str, Any]]:
    spans = provider.extract_entities(text, labels=labels, model=model_version)
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
    """For each span: MERGE the alias node, ensure :RESOLVED_TO target
    exists (resolution.resolve_alias_to_entity is the canonical mint),
    then MERGE the :MENTIONS edge with provenance properties."""
    if not spans:
        return 0
    cypher = """
    UNWIND $spans AS span
    MATCH (p:Post {uuid: $post_uuid})
    MERGE (al:Alias {surface_form: span.surface_form})
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
