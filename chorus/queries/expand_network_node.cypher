// expand_network_node — one hop of the bipartite Author<->Topic mention graph
// around a single, already-rendered node. Powers expand-on-click in the SPA:
// the client sends a namespaced node id it got from network_around (or a prior
// expansion) and receives that node's next-hop neighbours only — the clicked
// node itself is NOT returned (the client already has it).
//
// $kind/'$key' are pre-split by the tool from the namespaced id:
//   kind='author', key=<:Author.id>   -> topics the author mentions
//   kind='topic',  key=<topic key>    -> authors mentioning the topic
// A topic key is the resolved :Entity.id when present, else the alias surface
// form — the same key network_around bakes into "topic:<key>", so a clicked
// topic node round-trips without a name lookup. Topic identity follows the
// coalesce(entity, alias) rule used across the graph tools.
//
// Bounding: ranked by weight (distinct mentioning posts) desc, deterministic
// tiebreak (topic key / author id asc), sliced to $limit in-query; `truncated`
// is true when the cap dropped rows. Exactly one row is always returned.

// ---- author kind: the topics this author mentions. ----
CALL {
  UNWIND CASE WHEN $kind = 'author' THEN [$key] ELSE [] END AS aid
  MATCH (a:Author {id: aid})-[:AUTHORED]->(p:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH p,
    CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END AS t_key,
    CASE WHEN m:Entity THEN m.canonical_name
         WHEN e IS NOT NULL THEN e.canonical_name
         ELSE m.surface_form END AS t_label,
    CASE WHEN m:Entity THEN m.id ELSE e.id END AS t_eid
  WHERE t_key IS NOT NULL
  WITH t_key, t_label, t_eid, count(DISTINCT p) AS weight
  ORDER BY weight DESC, t_key ASC
  RETURN collect({topic_key: t_key, topic_label: t_label,
                  topic_entity_id: t_eid, weight: weight}) AS topics_ranked
}

// ---- topic kind: the authors mentioning this topic (matched by key). ----
CALL {
  UNWIND CASE WHEN $kind = 'topic' THEN [$key] ELSE [] END AS tkey
  MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH a, p, tkey,
    CASE WHEN m:Entity THEN m.id ELSE coalesce(e.id, m.surface_form) END AS m_key
  WHERE m_key = tkey
  WITH a, count(DISTINCT p) AS weight
  ORDER BY weight DESC, a.id ASC
  RETURN collect({author_id: a.id, handle: a.handle,
                  display_name: a.display_name, weight: weight}) AS authors_ranked
}

RETURN
  topics_ranked[0..$limit]  AS topics,
  authors_ranked[0..$limit] AS authors,
  (size(topics_ranked) > $limit OR size(authors_ranked) > $limit) AS truncated;
