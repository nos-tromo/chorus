// author_activity_summary — per-author aggregates over AUTHORED posts in an
// optional [from, to) window. A name may match multiple authors; each is its own
// row (callers must never merge distinct authors). Topic = resolved :Entity when
// present, else the :Alias surface form (today's mention target), mirroring
// posts_mentioning. Both CALL subqueries end in an aggregation/collect so each
// returns exactly one row even when the author has no posts — the author row is
// never dropped.

MATCH (a:Author)
WHERE toLower(coalesce(a.handle, "")) = toLower(trim($author))
   OR toLower(coalesce(a.display_name, "")) = toLower(trim($author))
CALL {
  WITH a
  OPTIONAL MATCH (a)-[:AUTHORED]->(p:Post)
    WHERE ($from IS NULL OR p.timestamp >= datetime($from))
      AND ($to   IS NULL OR p.timestamp <  datetime($to))
  RETURN
    count(p)                                AS post_count,
    count(CASE WHEN p:Posting THEN 1 END)   AS posting_count,
    count(CASE WHEN p:Comment THEN 1 END)   AS comment_count,
    count(CASE WHEN p:Message THEN 1 END)   AS message_count,
    min(p.timestamp)                        AS first_activity,
    max(p.timestamp)                        AS last_activity,
    sum(coalesce(p.expected_reactions, 0))  AS expected_reactions_total,
    sum(coalesce(p.collected_reactions, 0)) AS collected_reactions_total,
    sum(coalesce(p.expected_comments, 0))   AS expected_comments_total,
    sum(coalesce(p.collected_comments, 0))  AS collected_comments_total
}
CALL {
  WITH a
  OPTIONAL MATCH (a)-[:AUTHORED]->(p2:Post)
    WHERE ($from IS NULL OR p2.timestamp >= datetime($from))
      AND ($to   IS NULL OR p2.timestamp <  datetime($to))
  OPTIONAL MATCH (p2)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH p2,
    CASE WHEN m:Entity THEN m.canonical_name
         WHEN e IS NOT NULL THEN e.canonical_name
         ELSE m.surface_form END AS topic,
    CASE WHEN m:Entity THEN m.id ELSE e.id END AS entity_id
  WHERE topic IS NOT NULL
  WITH topic, entity_id, count(DISTINCT p2) AS cnt
  ORDER BY cnt DESC, topic ASC
  LIMIT 10
  RETURN collect({topic: topic, entity_id: entity_id, count: cnt}) AS top_topics
}
RETURN
  a.id           AS author_id,
  a.handle       AS handle,
  a.display_name AS display_name,
  a.platform     AS platform,
  post_count, posting_count, comment_count, message_count,
  first_activity, last_activity,
  expected_reactions_total, collected_reactions_total,
  expected_comments_total, collected_comments_total,
  top_topics
ORDER BY author_id;
