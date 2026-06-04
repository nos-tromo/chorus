// topic_co_occurrence — topics co-mentioned with a seed topic in the same posts
// (1-hop), within an optional [from, to) window, ranked by shared-post count.
//
// Topic identity ("ident") is the resolved :Entity id when the mention's :Alias
// resolved, else the lowercased :Alias surface form. The seed string is first
// resolved to the set of idents it denotes (Phase 1) — it matches an alias
// surface form OR a resolved entity's canonical name (case-insensitive) — so a
// query keeps working after its alias resolves, and seeding by one surface form
// spans posts that mention the same entity via a sibling surface form. The seed
// is excluded from its own list by ident, not by display name.

WITH toLower(trim($topic)) AS q
CALL {
  WITH q
  // aliases whose surface form is the seed → their entity id if resolved, else the form itself
  OPTIONAL MATCH (a:Alias) WHERE toLower(a.surface_form) = q
  OPTIONAL MATCH (a)-[:RESOLVED_TO]->(ae:Entity)
  WITH q, collect(DISTINCT CASE WHEN ae IS NOT NULL THEN ae.id ELSE toLower(a.surface_form) END) AS alias_idents
  // entities whose canonical name is the seed
  OPTIONAL MATCH (en:Entity) WHERE toLower(en.canonical_name) = q
  WITH alias_idents, collect(DISTINCT en.id) AS entity_idents
  WITH alias_idents + entity_idents AS seed_idents
  RETURN [x IN seed_idents WHERE x IS NOT NULL] AS seed_idents
}

MATCH (p:Post)-[:MENTIONS]->(m)
  WHERE ($from IS NULL OR p.timestamp >= datetime($from))
    AND ($to   IS NULL OR p.timestamp <  datetime($to))
OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH seed_idents, p,
  CASE WHEN m:Entity THEN m.canonical_name
       WHEN e IS NOT NULL THEN e.canonical_name
       ELSE m.surface_form END AS topic,
  CASE WHEN m:Entity THEN m.id ELSE e.id END AS entity_id,
  CASE WHEN m:Entity THEN m.id
       WHEN e IS NOT NULL THEN e.id
       ELSE toLower(m.surface_form) END AS ident
WITH seed_idents, p, collect({topic: topic, entity_id: entity_id, ident: ident}) AS topics
WHERE any(t IN topics WHERE t.ident IN seed_idents)
UNWIND topics AS t
WITH seed_idents, p, t
WHERE NOT t.ident IN seed_idents
WITH t.topic AS topic, t.entity_id AS entity_id, count(DISTINCT p) AS count
ORDER BY count DESC, topic ASC
LIMIT $limit
RETURN topic, entity_id, count;
