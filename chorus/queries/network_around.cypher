// network_around — the bipartite Author<->Topic ego network around a seed topic.
//
// Returns a renderer-ready node/edge graph (assembled in the tool module from the
// single row this query yields). Topic identity follows the coalesce(entity, alias)
// rule used across the graph tools: a topic is the resolved :Entity when present,
// else the :Alias surface form, so the network improves automatically once entity
// resolution runs — no query change.
//
// Rings, from the seed topic S:
//   depth 1: S + the authors who mention S            (star: who talks about X)
//   depth 2: + the other topics those authors mention (topic -> authors -> co-topics)
// The depth-1 star edges (author -> S) are built in the tool from each ring-1
// author's seed mention count; this query returns the depth-2 expansion edges only
// (empty when $depth < 2).
//
// Matching mirrors posts_mentioning / authors_mentioning verbatim (an :Entity by
// canonical_name, or an :Alias by surface_form or its resolved entity's
// canonical_name, all case-insensitive), so the depth-1 author set equals
// authors_mentioning($entity). Reuse the exact AND/OR parenthesisation — an
// unparenthesised OR is the precedence bug the sibling tools guard against.
//
// Bounding (both deterministic, in-query): the author ring is ranked by seed
// mention count desc (tiebreak author id) and sliced to $limit; second-ring topics
// are ranked by total edge weight desc (tiebreak key) and sliced to $topic_limit,
// with the seed topic always retained. `truncated` is true when either cap dropped
// nodes.
//
// Ambiguity note: if $entity matches both a resolved :Entity and a distinct
// unresolved :Alias of the same surface form, they are genuinely separate nodes in
// the graph (resolution has not merged them); the tool picks one as the seed and the
// other may surface as its own co-topic. Honest, and rare.

// ---- Seed identity: every matching mention variant, collapsed to candidates. ----
CALL {
  MATCH (:Post)-[:MENTIONS]->(sm)
  OPTIONAL MATCH (sm:Alias)-[:RESOLVED_TO]->(se:Entity)
  WITH labels(sm) AS sml, sm, se, trim($entity) AS q
  WHERE (
      ("Entity" IN sml AND toLower(coalesce(sm.canonical_name, "")) = toLower(q))
   OR ("Alias"  IN sml AND (
          toLower(coalesce(sm.surface_form, "")) = toLower(q)
       OR toLower(coalesce(se.canonical_name, "")) = toLower(q)))
  )
  RETURN collect(DISTINCT {
    key:       CASE WHEN "Entity" IN sml THEN sm.id ELSE coalesce(se.id, sm.surface_form) END,
    label:     CASE WHEN "Entity" IN sml THEN sm.canonical_name
                    WHEN se IS NOT NULL THEN se.canonical_name
                    ELSE sm.surface_form END,
    entity_id: CASE WHEN "Entity" IN sml THEN sm.id ELSE se.id END
  }) AS seed_variants
}

// ---- Ring 1: authors who mention the seed, ranked desc by mention count. ----
CALL {
  MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(m)
  OPTIONAL MATCH (m:Alias)-[:RESOLVED_TO]->(e:Entity)
  WITH a, p, labels(m) AS ml, m, e, trim($entity) AS q
  WHERE (
      ("Entity" IN ml AND toLower(coalesce(m.canonical_name, "")) = toLower(q))
   OR ("Alias"  IN ml AND (
          toLower(coalesce(m.surface_form, "")) = toLower(q)
       OR toLower(coalesce(e.canonical_name, "")) = toLower(q)))
  )
  WITH a, count(DISTINCT p) AS w_seed
  ORDER BY w_seed DESC, a.id ASC
  RETURN collect({
    author_id: a.id, handle: a.handle, display_name: a.display_name, w_seed: w_seed
  }) AS authors_ranked
}
WITH seed_variants, authors_ranked,
     authors_ranked[0..$limit]   AS ring1,
     size(authors_ranked) > $limit AS authors_truncated

// ---- Ring 2 (depth >= 2): the topics the ring-1 authors mention. ----
WITH seed_variants, ring1, authors_truncated,
     [r IN ring1 | r.author_id] AS ring1_ids,
     [v IN seed_variants | v.key] AS seed_keys
CALL (ring1_ids) {
  UNWIND CASE WHEN $depth >= 2 THEN ring1_ids ELSE [] END AS aid
  MATCH (a:Author {id: aid})-[:AUTHORED]->(p2:Post)-[:MENTIONS]->(m2)
  OPTIONAL MATCH (m2:Alias)-[:RESOLVED_TO]->(e2:Entity)
  WITH aid, p2,
    CASE WHEN m2:Entity THEN m2.id ELSE coalesce(e2.id, m2.surface_form) END AS t_key,
    CASE WHEN m2:Entity THEN m2.canonical_name
         WHEN e2 IS NOT NULL THEN e2.canonical_name
         ELSE m2.surface_form END AS t_label,
    CASE WHEN m2:Entity THEN m2.id ELSE e2.id END AS t_eid
  WHERE t_key IS NOT NULL
  WITH aid, t_key, t_label, t_eid, count(DISTINCT p2) AS weight
  RETURN collect({
    author_id: aid, topic_key: t_key, topic_label: t_label,
    topic_entity_id: t_eid, weight: weight
  }) AS all_edges
}

// ---- Cap non-seed topics to $topic_limit by total weight (seed always kept). ----
WITH seed_variants, ring1, authors_truncated, seed_keys, all_edges,
     [ed IN all_edges WHERE NOT ed.topic_key IN seed_keys] AS nonseed_edges
CALL (nonseed_edges) {
  UNWIND nonseed_edges AS ed
  WITH ed.topic_key AS tk, sum(ed.weight) AS tw
  ORDER BY tw DESC, tk ASC
  RETURN collect(tk) AS ranked_topic_keys
}
WITH seed_variants, ring1, authors_truncated, seed_keys, all_edges,
     ranked_topic_keys[0..$topic_limit]   AS kept_topic_keys,
     size(ranked_topic_keys) > $topic_limit AS topics_truncated

RETURN
  seed_variants,
  ring1,
  [ed IN all_edges WHERE ed.topic_key IN seed_keys OR ed.topic_key IN kept_topic_keys] AS edges,
  (authors_truncated OR topics_truncated) AS truncated;
