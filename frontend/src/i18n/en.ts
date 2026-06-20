export const en = {
  // common (shared across pages)
  'common.tool_call_failed': 'tool call failed: {error}',
  'common.unreachable': 'unreachable: {error}',
  'common.entity_input': 'Entity name or alias',
  'common.limit': 'Limit',
  'common.from_ts': 'From (ISO timestamp, optional)',
  'common.to_ts': 'To (ISO timestamp, optional)',
  'common.search': 'Search',
  'common.resolution_note':
    'Topics cluster by canonical entity after a resolution pass; on ' +
    'un-resolved data they show as alias surface forms.',
  // landing (streamlit_app.py)
  'landing.caption': 'GraphRAG for social network analysis',
  'landing.backend_health': 'backend health',
  'landing.registered_tools': 'registered tools',
  'landing.pick_tool': 'Pick a tool from the sidebar (left) to explore the graph.',
  'landing.ingestion_on':
    'Data ingestion is enabled — see the Data ingestion page in the sidebar.',
  'landing.ingestion_off': 'Data ingestion is disabled.',
  // agent (00_agent.py)
  'agent.title': 'chorus agent',
  'agent.caption':
    'Ask in plain language; the agent picks the right tools. Topics ' +
    'cluster by canonical entity after a resolution pass; on un-resolved ' +
    'data they show as alias surface forms.',
  'agent.clear': 'Clear conversation',
  'agent.chat_input': 'Ask a question about the network…',
  'agent.thinking': 'Thinking…',
  'agent.tool_calls': 'Tool calls ({n})',
  'agent.trace_error_label': '— error:',
  'agent.trace_results': ' — {count} result(s)',
  'agent.call_failed': 'agent call failed: {error}',
  'agent.no_answer': '(no answer)',
  'agent.truncated':
    'Stopped at the tool-call limit before reaching a final answer.',
  // posts_mentioning (01)
  'posts.title': 'posts mentioning an entity',
  'posts.no_hits': 'no hits',
  // author_activity_summary (02)
  'author_activity.title': 'author activity summary',
  'author_activity.author_input': 'Author handle or display name',
  'author_activity.summarize': 'Summarize',
  'author_activity.matched': '{n} author(s) matched',
  'author_activity.no_topics': 'no topics mentioned in range',
  'author_activity.no_author': 'no matching author',
  // topic_co_occurrence (03)
  'topic_cooc.title': 'topic co-occurrence',
  'topic_cooc.seed_input': 'Seed topic (entity or alias)',
  'topic_cooc.none': 'no co-occurring topics',
  // authors_connected_by_topic (04)
  'authors_connected.title': 'authors connected by topic',
  'authors_connected.caption':
    '1-hop only. Topics cluster by canonical entity after a resolution ' +
    'pass; on un-resolved data they show as alias surface forms.',
  'authors_connected.seed_author_input': 'Seed author handle or display name',
  'authors_connected.min_overlap': 'Minimum shared topics',
  'authors_connected.limit': 'Limit (per matched seed)',
  'authors_connected.find': 'Find connected authors',
  'authors_connected.no_seed': 'no matching seed author',
  'authors_connected.connected_count': '{label}  ·  {n} connected',
  'authors_connected.none':
    'no connected authors at this overlap threshold',
  // authors_mentioning (05)
  'authors_mentioning.title': 'authors mentioning an entity',
  'authors_mentioning.caption':
    'Authors ranked by how many of their posts mention the entity.',
  'authors_mentioning.none': 'no authors',
  // network_around (06)
  'network.title': 'network around an entity',
  'network.caption':
    'Bipartite author-topic network. depth 1 = the authors who mention ' +
    'the entity; depth 2 also adds the other topics those authors ' +
    'mention. The view is capped by the limits below. Topics cluster by ' +
    'canonical entity once a resolution pass has run; on unresolved data ' +
    'they are raw alias surface forms.',
  'network.depth': 'Depth',
  'network.author_limit': 'Author limit',
  'network.topic_limit': 'Topic limit (depth 2)',
  'network.build': 'Build network',
  'network.empty': 'no network — the entity matched nothing',
  'network.counts':
    '{n} node(s): {authors} author(s), {topics} topic(s); {edges} edge(s)',
  'network.capped':
    'Capped view — raise the author/topic limits to see more of the network.',
  // graph canvas
  'graph.fit': 'Fit',
  // social_network_around (08)
  'social.title': 'social network around an author',
  'social.caption':
    "The follows/friends ego network around an author. depth 1 = the author's direct " +
    'ties (people they follow, their followers, friends); depth 2 also adds those ' +
    "neighbours' ties. Follows are drawn with arrowheads, friendships as plain lines. " +
    'The view is capped by the limits below.',
  'social.author_input': 'Author handle or display name',
  'social.depth': 'Depth',
  'social.limit': 'Direct-tie limit',
  'social.second_ring_limit': 'Second-ring limit (depth 2)',
  'social.build': 'Build network',
  'social.empty': 'no network — the author matched nothing',
  'social.counts':
    '{n} author(s); {edges} tie(s): {follows} follows, {friends} friends',
  'social.capped': 'Capped view — raise the limits to see more of the network.',
  // data ingestion (01_data_ingestion.py)
  'ingest.title': 'Data ingestion',
  'ingest.caption':
    'Upload table exports and run the pipeline end-to-end: migrate, ingest, resolve. ' +
    'Large bulk loads still belong on the server via `make ingest`.',
  'ingest.disabled':
    'Data ingestion via the UI is disabled. Set INGESTION_UI_ENABLED=true on the backend to enable it.',
  'ingest.migrations.header': 'Schema migrations',
  'ingest.migrations.pending': 'Pending migrations: {versions}',
  'ingest.migrations.apply': 'Apply migrations',
  'ingest.migrations.applying': 'Applying migrations…',
  'ingest.migrations.applied': 'Applied: {versions}',
  'ingest.migrations.uptodate': 'Schema is up to date.',
  'ingest.upload.header': 'Upload & ingest',
  'ingest.upload.help':
    'Filenames must match a known table: postings.csv, comments.csv, messages.csv, ' +
    'profiles.csv, connections.csv (or segmented like 2026-05_postings.csv).',
  'ingest.upload.label': 'CSV table exports',
  'ingest.upload.since': 'Only rows newer than (ISO timestamp, optional)',
  'ingest.upload.then_resolve': 'Run resolution after ingestion',
  'ingest.upload.start': 'Start ingestion',
  'ingest.job.running':
    'Ingestion running… this page refreshes automatically.',
  'ingest.job.done': 'Ingestion complete.',
  'ingest.job.failed': 'Ingestion failed: {error}',
  'ingest.counts.header': 'Rows ingested',
  'ingest.counts.dropped': 'Dropped malformed rows: {detail}',
  'ingest.counts.filtered':
    'Filtered rows (no in-batch parent / no edge signal): {detail}',
  'ingest.counts.skipped': 'Skipped stages: {detail}',
  'ingest.resolve.header': 'Entity resolution',
  'ingest.resolve.start': 'Run resolution',
  'ingest.resolve.running':
    'Resolution running… this page refreshes automatically.',
  'ingest.resolve.done': 'Resolution complete.',
  'ingest.resolve.failed': 'Resolution failed: {error}',
  'ingest.resolve.summary': 'Resolution summary',
  'ingest.error.request': 'Request rejected: {detail}',
  // sidebar nav group headings
  'nav.group.entities': 'Entities',
  'nav.group.authors': 'Authors',
  'nav.group.topics': 'Topics',
  'nav.group.networks': 'Networks',
} as const
