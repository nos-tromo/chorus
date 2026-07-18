// expand_social_node — the direct social ties (:FOLLOWS directed,
// :FRIENDS_WITH undirected) of one author, seeded by :Author.id. Powers
// expand-on-click in the SPA's social explorer: the ring-1 block of
// social_network_around, seeded by id instead of name, returning the clicked
// author's neighbours + the connecting edges only (the clicked author is not
// re-returned).
//
// Edge identity is intrinsic to the relationship, matching
// social_network_around verbatim:
//   :FOLLOWS      -> directed, src = startNode (follower), dst = endNode (followee)
//   :FRIENDS_WITH -> undirected, emitted canonically (lower id = src), directed=false
//
// Bounding: neighbours ranked by social degree desc (tiebreak author id asc),
// sliced to $limit in-query; `truncated` true when the cap dropped nodes.
// Yields exactly one row when the author exists, zero rows otherwise.

MATCH (seed:Author {id: $author_id})

CALL (seed) {
  MATCH (seed)-[:FOLLOWS|FRIENDS_WITH]-(nb:Author)
  WHERE nb <> seed
  WITH DISTINCT nb
  WITH nb, COUNT { (nb)-[:FOLLOWS|FRIENDS_WITH]-() } AS deg
  ORDER BY deg DESC, nb.id ASC
  RETURN collect(nb.id) AS ranked
}
WITH seed,
     ranked[0..$limit]     AS kept,
     size(ranked) > $limit AS truncated

CALL (seed, kept) {
  MATCH (seed)-[r:FOLLOWS|FRIENDS_WITH]-(b:Author)
  WHERE b.id IN kept
  WITH DISTINCT r, b
  RETURN collect({
    src:      CASE WHEN type(r) = 'FOLLOWS' THEN startNode(r).id
                   WHEN seed.id < b.id THEN seed.id ELSE b.id END,
    dst:      CASE WHEN type(r) = 'FOLLOWS' THEN endNode(r).id
                   WHEN seed.id < b.id THEN b.id ELSE seed.id END,
    kind:     CASE WHEN type(r) = 'FOLLOWS' THEN 'follows' ELSE 'friends' END,
    directed: type(r) = 'FOLLOWS'
  }) AS edges
}

CALL (kept) {
  UNWIND kept AS rid
  MATCH (a:Author {id: rid})
  RETURN collect({id: a.id, handle: a.handle, display_name: a.display_name}) AS neighbours
}

RETURN neighbours, edges, truncated;
