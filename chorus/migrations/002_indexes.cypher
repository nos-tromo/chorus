// 002_indexes — btree + relationship indexes.
// FOLLOWS / FRIENDS_WITH indexes land now so the deferred connections
// ingestion (see ADR 0002) finds an index-backed MERGE target when it
// eventually runs against millions of rows.

CREATE INDEX post_timestamp IF NOT EXISTS
  FOR (p:Post) ON (p.timestamp);

CREATE INDEX post_retention IF NOT EXISTS
  FOR (p:Post) ON (p.retention_until);

CREATE INDEX author_handle IF NOT EXISTS
  FOR (a:Author) ON (a.handle);

CREATE INDEX entity_canonical IF NOT EXISTS
  FOR (e:Entity) ON (e.canonical_name);

CREATE INDEX follows_crawled IF NOT EXISTS
  FOR ()-[r:FOLLOWS]-() ON (r.crawled_at);

CREATE INDEX friends_crawled IF NOT EXISTS
  FOR ()-[r:FRIENDS_WITH]-() ON (r.crawled_at);

CREATE INDEX mentions_model_version IF NOT EXISTS
  FOR ()-[r:MENTIONS]-() ON (r.model_version);
