// posts_mentioning — return posts that MENTION a canonical entity name,
// optionally constrained to a [from, to) time window.
// Reuses :Post (matches all three artifact types: Posting, Comment, Message).

MATCH (p:Post)-[:MENTIONS]->(e:Entity {canonical_name: $entity})
WHERE ($from IS NULL OR p.timestamp >= datetime($from))
  AND ($to   IS NULL OR p.timestamp <  datetime($to))
RETURN
  p.uuid       AS uuid,
  p.text       AS text,
  p.timestamp  AS ts,
  labels(p)    AS labels,
  e.id         AS entity_id
ORDER BY p.timestamp DESC
LIMIT $limit;
