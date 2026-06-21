// Graph diagnostics — single round-trip, independent CALL{} subqueries.
// Each subquery is self-contained so an empty graph yields 0 / [] / null
// for every field without raising.  Neo4j 5.x CALL{} syntax throughout.

CALL {
    MATCH (p:Post) RETURN count(p) AS post_count
}
CALL {
    MATCH (a:Author) RETURN count(a) AS author_count
}
CALL {
    MATCH (e:Entity) RETURN count(e) AS entity_count
}
CALL {
    MATCH (h:Hashtag) RETURN count(h) AS hashtag_count
}
CALL {
    MATCH (g:Group) RETURN count(g) AS group_count
}
CALL {
    MATCH (pl:Platform) RETURN count(pl) AS platform_count
}
CALL {
    MATCH (al:Alias) RETURN count(al) AS alias_count
}
CALL {
    MATCH ()-[r:MENTIONS]->() RETURN count(r) AS mentions_count
}
CALL {
    MATCH ()-[r:AUTHORED]->() RETURN count(r) AS authored_count
}
CALL {
    MATCH ()-[r:FOLLOWS]->() RETURN count(r) AS follows_count
}
CALL {
    MATCH ()-[r:FRIENDS_WITH]->() RETURN count(r) AS friends_count
}
CALL {
    MATCH ()-[r:RESOLVED_TO]->() RETURN count(r) AS resolved_count
}
CALL {
    OPTIONAL MATCH (p:Post)
    RETURN max(p.ingested_at) AS latest_ingested_at
}
CALL {
    MATCH (al:Alias) RETURN count(al) AS total_aliases
}
CALL {
    MATCH (al:Alias)-[:RESOLVED_TO]->()
    RETURN count(DISTINCT al) AS resolved_aliases
}
CALL {
    OPTIONAL MATCH (p:Post)-[:MENTIONS]->(al:Alias)
    OPTIONAL MATCH (al)-[:RESOLVED_TO]->(e:Entity)
    WITH coalesce(e.canonical_name, al.surface_form) AS name, count(p) AS c
    WHERE name IS NOT NULL
    ORDER BY c DESC
    RETURN collect({name: name, count: c})[..5] AS top_entities
}
CALL {
    OPTIONAL MATCH (a:Author)-[:AUTHORED]->(p:Post)
    WITH a, count(p) AS c
    WHERE a IS NOT NULL
    ORDER BY c DESC
    LIMIT 5
    RETURN collect({
        author_id: a.id,
        label: coalesce(a.display_name, a.handle, a.id),
        count: c
    }) AS top_authors
}
CALL {
    OPTIONAL MATCH (p:Post)-[:ON_PLATFORM]->(pl:Platform)
    WITH pl.name AS platform, count(p) AS c
    RETURN collect({platform: platform, count: c}) AS posts_by_platform
}

RETURN
    post_count,
    author_count,
    entity_count,
    hashtag_count,
    group_count,
    platform_count,
    alias_count,
    mentions_count,
    authored_count,
    follows_count,
    friends_count,
    resolved_count,
    latest_ingested_at,
    total_aliases,
    resolved_aliases,
    top_entities,
    top_authors,
    posts_by_platform
