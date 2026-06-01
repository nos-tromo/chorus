// posts_mentioning — return posts that mention either:
// - an :Entity whose canonical_name matches the query, or
// - an :Alias whose surface_form matches the query, optionally resolved
//   onward to an :Entity.
// Matching is case-insensitive so the UI can accept human input without
// requiring exact graph casing.
// Comment/message ingestion may create thin :Post stubs for parent links
// before the full row arrives. This tool only returns fully materialized
// posts because downstream output requires body text and timestamp.

MATCH (p:Post)-[:MENTIONS]->(mention)
OPTIONAL MATCH (mention:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH p, mention, e, labels(mention) AS mention_labels, trim($entity) AS entity_query
WHERE (
    (
        "Entity" IN mention_labels
        AND toLower(coalesce(mention.canonical_name, "")) = toLower(entity_query)
    ) OR (
        "Alias" IN mention_labels
        AND (
            toLower(coalesce(mention.surface_form, "")) = toLower(entity_query)
            OR toLower(coalesce(e.canonical_name, "")) = toLower(entity_query)
        )
    )
)
  AND ($from IS NULL OR p.timestamp >= datetime($from))
  AND ($to   IS NULL OR p.timestamp <  datetime($to))
  AND p.text IS NOT NULL
  AND p.timestamp IS NOT NULL
RETURN
  p.uuid       AS uuid,
  p.text       AS text,
  p.timestamp  AS ts,
  labels(p)    AS labels,
  CASE
    WHEN "Entity" IN mention_labels THEN mention.id
    ELSE e.id
  END          AS entity_id,
  CASE
    WHEN "Entity" IN mention_labels THEN mention.canonical_name
    WHEN e IS NOT NULL THEN e.canonical_name
    ELSE mention.surface_form
  END          AS matched_name
ORDER BY p.timestamp DESC
LIMIT $limit;
