// authors_mentioning — authors ranked by how many of their posts mention the query
// entity, within an optional [from, to) window.
//
// The MENTIONS-target match mirrors posts_mentioning.cypher verbatim (an :Entity by
// canonical_name, or an :Alias by surface_form or its resolved entity's
// canonical_name, all case-insensitive) so authors_mentioning(X) returns precisely
// the authors behind the posts posts_mentioning(X) returns. Unlike posts_mentioning
// there is no `text/timestamp IS NOT NULL` filter: this tool returns neither body
// text nor a time ordering, and a mention on a timestamp-less post is still a real
// mention. (Lockstep with posts_mentioning therefore holds for timestamped,
// text-bearing posts; a text-null mentioning post — which NER does not produce in
// practice, as extraction runs on text — would count here but be dropped by
// posts_mentioning's text filter.) count(DISTINCT p) collapses a post matched via
// several aliases/entities
// (or an alias with several :RESOLVED_TO edges) to a single contribution. Counts
// span every :Post the author authored — postings, comments, and messages.

MATCH (a:Author)-[:AUTHORED]->(p:Post)-[:MENTIONS]->(mention)
OPTIONAL MATCH (mention:Alias)-[:RESOLVED_TO]->(e:Entity)
WITH a, p, mention, e, labels(mention) AS mention_labels, trim($entity) AS entity_query
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
WITH a,
  count(DISTINCT p)                                                                    AS mention_post_count,
  min(p.timestamp)                                                                     AS first_mention,
  max(p.timestamp)                                                                     AS last_mention,
  collect(DISTINCT CASE WHEN "Entity" IN mention_labels THEN mention.id ELSE e.id END) AS raw_entity_ids
RETURN
  a.id           AS author_id,
  a.handle       AS handle,
  a.display_name AS display_name,
  a.platform     AS platform,
  mention_post_count,
  first_mention,
  last_mention,
  [x IN raw_entity_ids WHERE x IS NOT NULL] AS entity_ids
ORDER BY mention_post_count DESC, author_id ASC
LIMIT $limit;
