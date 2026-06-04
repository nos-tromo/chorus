// authors_connected_by_topic — other authors mentioning the same topic keys as
// the seed author, ranked by overlap (number of shared topic keys, >= $min_overlap).
// 1-hop. Topic key = resolved :Entity id when present, else the :Alias surface form
// (today's mention target), mirroring posts_mentioning. A seed name may match many
// authors; results are grouped per matched seed. Both CALL subqueries end in a
// collect so every matched seed yields exactly one row with a (possibly empty)
// connected list — seeds with no topics or no matches are never dropped.
// Resolved-topic entity ids are collected per connected author (nulls dropped)
// to feed the §76 audit trail; unresolved aliases contribute no id.

MATCH (seed:Author)
WHERE toLower(coalesce(seed.handle, "")) = toLower(trim($seed_author))
   OR toLower(coalesce(seed.display_name, "")) = toLower(trim($seed_author))
CALL {
  WITH seed
  OPTIONAL MATCH (seed)-[:AUTHORED]->(:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END AS key
  WHERE key IS NOT NULL
  RETURN collect(DISTINCT key) AS seed_keys
}
CALL {
  WITH seed, seed_keys
  MATCH (other:Author)-[:AUTHORED]->(:Post)-[:MENTIONS]->(m2)
    WHERE other <> seed
  OPTIONAL MATCH (m2:Alias)-[:RESOLVED_TO]->(e2:Entity)
  WITH seed_keys, other,
    CASE WHEN m2:Entity THEN m2.id ELSE coalesce(e2.id, m2.surface_form) END AS key2,
    CASE WHEN m2:Entity THEN m2.canonical_name
         WHEN e2 IS NOT NULL THEN e2.canonical_name
         ELSE m2.surface_form END AS name2,
    CASE WHEN m2:Entity THEN m2.id ELSE e2.id END AS entity_id2
  WHERE key2 IN seed_keys
  WITH other,
    collect(DISTINCT name2) AS shared_topics,
    collect(DISTINCT entity_id2) AS shared_entity_ids,
    count(DISTINCT key2) AS overlap
  WHERE overlap >= $min_overlap
  ORDER BY overlap DESC, other.id ASC
  LIMIT $limit
  RETURN collect({
    author_id: other.id, handle: other.handle, display_name: other.display_name,
    overlap: overlap, shared_topics: shared_topics, shared_entity_ids: shared_entity_ids
  }) AS connected
}
RETURN
  seed.id           AS seed_author_id,
  seed.handle       AS seed_handle,
  seed.display_name AS seed_display_name,
  connected
ORDER BY seed_author_id;
