// topic_co_occurrence — topics co-mentioned with a seed topic in the same posts
// (1-hop), within an optional [from, to) window, ranked by shared-post count.
// Topic = resolved :Entity when present, else the :Alias surface form (today's
// mention target), mirroring posts_mentioning. The seed is matched by name
// (case-insensitive) and excluded from its own co-occurrence list.

MATCH (p:Post)-[:MENTIONS]->(m)
  WHERE ($from IS NULL OR p.timestamp >= datetime($from))
    AND ($to   IS NULL OR p.timestamp <  datetime($to))
OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH p, toLower(trim($topic)) AS q,
  CASE WHEN m:Entity THEN m.canonical_name
       WHEN e IS NOT NULL THEN e.canonical_name
       ELSE m.surface_form END AS topic,
  CASE WHEN m:Entity THEN m.id ELSE e.id END AS entity_id
WITH p, q, collect({topic: topic, entity_id: entity_id}) AS topics
WITH p, q, topics,
  [t IN topics WHERE toLower(coalesce(t.topic, "")) = q] AS seed_hits
WHERE size(seed_hits) > 0
UNWIND topics AS t
WITH q, p, t
WHERE toLower(coalesce(t.topic, "")) <> q
WITH t.topic AS topic, t.entity_id AS entity_id, count(DISTINCT p) AS count
ORDER BY count DESC, topic ASC
LIMIT $limit
RETURN topic, entity_id, count;
