// social_network_around — the author ego network over the social graph
// (:FOLLOWS directed, :FRIENDS_WITH undirected), returned as a renderer-ready
// node/edge graph. The social twin of network_around (which is the topic ego
// network). Seeds on an :Author; traverses social ties, not mentions.
//
// Rings from the seed author S:
//   depth 1: S + its direct ties (people S follows, people who follow S, friends)
//   depth 2: + the ties of those neighbours (radial — ring-1 -> ring-2 only)
// Only radial edges are emitted (S<->ring1, ring1<->ring2); intra-ring edges are
// deferred. depth > 2 is rejected at input validation (the tool), not here.
//
// Edge identity is intrinsic to the relationship, independent of which endpoint
// we expand from:
//   :FOLLOWS      -> directed, src = startNode (follower), dst = endNode (followee)
//   :FRIENDS_WITH -> undirected, emitted canonically (lower id = src), directed=false
// Each edge subquery anchors on a bound node (seed, or a ring-1 author), so an
// undirected match returns each incident relationship once (no double-count).
//
// Bounding (both deterministic, in-query): each ring is ranked by social degree
// desc (COUNT of incident :FOLLOWS/:FRIENDS_WITH — the hubs), tiebreak author id
// asc, and sliced to its cap ($limit for ring 1, $second_ring_limit for ring 2).
// `truncated` is true when either cap dropped nodes.
//
// Every CALL ends in a collect so the query yields exactly one row whenever the
// seed matched — a seed with no ties still returns itself with empty rings/edges.

// ---- Seed: one ego, deterministic on ambiguous names. ----
// A name may match several authors; prefer a handle match over a display-name
// match, then the lowest id, and take exactly one.
MATCH (seed:Author)
WITH seed, toLower(trim($author)) AS q
WHERE toLower(coalesce(seed.handle, "")) = q
   OR toLower(coalesce(seed.display_name, "")) = q
WITH seed, (toLower(coalesce(seed.handle, "")) = q) AS handle_match
ORDER BY handle_match DESC, seed.id ASC
LIMIT 1

// ---- Ring 1 node set: seed's neighbours, ranked by social degree, capped. ----
CALL (seed) {
  MATCH (seed)-[:FOLLOWS|FRIENDS_WITH]-(nb:Author)
  WHERE nb <> seed
  WITH DISTINCT nb
  WITH nb, COUNT { (nb)-[:FOLLOWS|FRIENDS_WITH]-() } AS deg
  ORDER BY deg DESC, nb.id ASC
  RETURN collect(nb.id) AS r1_ranked
}
WITH seed, r1_ranked,
     r1_ranked[0..$limit]          AS kept_r1,
     size(r1_ranked) > $limit      AS r1_truncated

// ---- Ring 2 node set (depth >= 2): neighbours of ring 1, minus seed and ring 1. ----
CALL (seed, kept_r1) {
  UNWIND CASE WHEN $depth >= 2 THEN kept_r1 ELSE [] END AS aid
  MATCH (a:Author {id: aid})-[:FOLLOWS|FRIENDS_WITH]-(nb2:Author)
  WHERE nb2 <> seed AND NOT nb2.id IN kept_r1
  WITH DISTINCT nb2
  WITH nb2, COUNT { (nb2)-[:FOLLOWS|FRIENDS_WITH]-() } AS deg
  ORDER BY deg DESC, nb2.id ASC
  RETURN collect(nb2.id) AS r2_ranked
}
WITH seed, kept_r1, r1_truncated,
     r2_ranked[0..$second_ring_limit]                       AS kept_r2,
     (r1_truncated OR size(r2_ranked) > $second_ring_limit) AS truncated

// ---- Ring 1 edges: seed <-> kept ring 1 (anchored on seed). ----
CALL (seed, kept_r1) {
  MATCH (seed)-[r:FOLLOWS|FRIENDS_WITH]-(b:Author)
  WHERE b.id IN kept_r1
  WITH DISTINCT r, b
  RETURN collect({
    src:      CASE WHEN type(r) = 'FOLLOWS' THEN startNode(r).id
                   WHEN seed.id < b.id THEN seed.id ELSE b.id END,
    dst:      CASE WHEN type(r) = 'FOLLOWS' THEN endNode(r).id
                   WHEN seed.id < b.id THEN b.id ELSE seed.id END,
    kind:     CASE WHEN type(r) = 'FOLLOWS' THEN 'follows' ELSE 'friends' END,
    directed: type(r) = 'FOLLOWS'
  }) AS edges_r1
}

// ---- Ring 2 edges: kept ring 1 <-> kept ring 2 (anchored on each ring-1 author). ----
CALL (seed, kept_r1, kept_r2) {
  UNWIND kept_r1 AS aid
  MATCH (a:Author {id: aid})-[r:FOLLOWS|FRIENDS_WITH]-(b:Author)
  WHERE b.id IN kept_r2
  WITH DISTINCT r, a, b
  RETURN collect({
    src:      CASE WHEN type(r) = 'FOLLOWS' THEN startNode(r).id
                   WHEN a.id < b.id THEN a.id ELSE b.id END,
    dst:      CASE WHEN type(r) = 'FOLLOWS' THEN endNode(r).id
                   WHEN a.id < b.id THEN b.id ELSE a.id END,
    kind:     CASE WHEN type(r) = 'FOLLOWS' THEN 'follows' ELSE 'friends' END,
    directed: type(r) = 'FOLLOWS'
  }) AS edges_r2
}

// ---- Resolve display fields for the kept node ids (order preserved). ----
CALL (kept_r1) {
  UNWIND kept_r1 AS rid
  MATCH (a:Author {id: rid})
  RETURN collect({id: a.id, handle: a.handle, display_name: a.display_name}) AS ring1
}
CALL (kept_r2) {
  UNWIND kept_r2 AS rid
  MATCH (a:Author {id: rid})
  RETURN collect({id: a.id, handle: a.handle, display_name: a.display_name}) AS ring2
}

RETURN
  seed.id            AS seed_id,
  seed.handle        AS seed_handle,
  seed.display_name  AS seed_display_name,
  ring1,
  ring2,
  edges_r1 + edges_r2 AS edges,
  truncated;
