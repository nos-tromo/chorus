// 004_alias_norm_key — durable cross-run case-variant clustering (issue #24).
// :Alias.norm_key is the normalized (trim + casefold) surface form, written by
// the resolution stage when it links an alias to an entity. MANY raw
// surface_forms ('Berlin', 'berlin', 'Berlin ') share one norm_key, so this is
// a NON-UNIQUE range index, NOT a constraint — the raw surface_form keeps its
// own UNIQUE constraint (alias_surface, migration 001). It backs the
// norm_key lookup in resolution.resolve_alias_to_entity. See ADR 0012.

CREATE INDEX alias_norm_key IF NOT EXISTS
  FOR (a:Alias) ON (a.norm_key);
