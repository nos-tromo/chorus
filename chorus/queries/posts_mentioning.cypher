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
WITH p,
  CASE
    WHEN "Entity" IN mention_labels THEN mention.id
    ELSE e.id
  END          AS entity_id,
  CASE
    WHEN "Entity" IN mention_labels THEN mention.canonical_name
    WHEN e IS NOT NULL THEN e.canonical_name
    ELSE mention.surface_form
  END          AS matched_name
// Collapse to one row per post. A post can fan out into several rows here: an
// :Alias with more than one :RESOLVED_TO edge (no cardinality constraint exists
// in Neo4j CE), or a post mentioning the query term via two aliases/entities.
// Without this, the same post is returned more than once and silently consumes
// $limit slots. Pick deterministically by entity_id (nulls sort last).
WITH p, entity_id, matched_name ORDER BY entity_id
WITH p, head(collect({entity_id: entity_id, matched_name: matched_name})) AS pick
RETURN
  p.uuid            AS uuid,
  p.text            AS text,
  p.timestamp       AS ts,
  labels(p)         AS labels,
  pick.entity_id    AS entity_id,
  pick.matched_name AS matched_name
ORDER BY p.timestamp DESC
LIMIT $limit;
