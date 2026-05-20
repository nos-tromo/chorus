// 001_constraints — uniqueness for graph keys.
// UUID is the primary key for posts/comments/messages (multi-label :Post).

CREATE CONSTRAINT post_uuid IF NOT EXISTS
  FOR (p:Post) REQUIRE p.uuid IS UNIQUE;

CREATE CONSTRAINT author_id IF NOT EXISTS
  FOR (a:Author) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT entity_id IF NOT EXISTS
  FOR (e:Entity) REQUIRE e.id IS UNIQUE;

CREATE CONSTRAINT hashtag_tag IF NOT EXISTS
  FOR (h:Hashtag) REQUIRE h.tag IS UNIQUE;

CREATE CONSTRAINT platform_name IF NOT EXISTS
  FOR (p:Platform) REQUIRE p.name IS UNIQUE;

CREATE CONSTRAINT group_id IF NOT EXISTS
  FOR (g:Group) REQUIRE g.id IS UNIQUE;

CREATE CONSTRAINT alias_surface IF NOT EXISTS
  FOR (a:Alias) REQUIRE a.surface_form IS UNIQUE;

CREATE CONSTRAINT migration_version IF NOT EXISTS
  FOR (m:_Migration) REQUIRE m.version IS UNIQUE;
