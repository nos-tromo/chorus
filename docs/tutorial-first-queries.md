# Tutorial: first queries

A guided first pass over the chorus surface — list the tools, seed one
row into an empty graph, invoke a tool directly, then ask the agent the
same question in natural language.

It assumes the API is already running and answering on
`http://localhost:8000` (see [Quick start](../README.md#quick-start) in
the top-level README, through step 4) and that migrations have been
applied. Every request below uses the dev identity, so `.env` needs
`CHORUS_DEFAULT_IDENTITY=dev` — production leaves that unset and requires
a real `X-Auth-User` header from the edge gateway.

## Tool registry

```bash
curl -s http://localhost:8000/tools | jq
```

Lists every registered tool with its Pydantic input/output schemas:
`posts_mentioning`, `authors_mentioning`, `author_activity_summary`,
`topic_co_occurrence`, `authors_connected_by_topic`, `network_around`,
and `social_network_around`.

## Seed a posting and an entity

The graph is empty after migrations, so the tool will return zero
hits. Seed one row directly via the Neo4j browser
(<http://localhost:7474>) or `cypher-shell`:

```cypher
MERGE (e:Entity {id: 'ent-berlin'})
  ON CREATE SET e.canonical_name = 'Berlin';
MERGE (p:Post:Posting {uuid: 'p-1'})
  ON CREATE SET p.text = 'hello berlin',
                p.timestamp = datetime();
MERGE (p)-[:MENTIONS]->(e);
```

## Invoke `posts_mentioning`

```bash
curl -s -X POST http://localhost:8000/tools/posts_mentioning \
  -H 'Content-Type: application/json' \
  -H 'X-Auth-User: dev' \
  -d '{"entity": "Berlin", "limit": 10}' | jq
```

You should see one hit referencing `p-1` and `ent-berlin`. The same
call writes one row to the audit log:

```bash
sqlite3 var/audit.sqlite \
  "SELECT user, tool_name, result_count, status FROM audit_log;"
```

The other tools are invoked the same way — `POST /tools/<name>` with
the body matching the schema from `/tools`.

## Ask the agent

The agent answers free-text questions by selecting and calling those
tools. The server is stateless, so the client sends the visible
conversation on each request. This path needs a reachable, OpenAI-compatible
inference endpoint that supports tool-calling (`OPENAI_API_BASE` /
`TEXT_MODEL`):

```bash
curl -s -X POST http://localhost:8000/agent/query \
  -H 'Content-Type: application/json' \
  -H 'X-Auth-User: dev' \
  -d '{"messages": [{"role": "user", "content": "Which posts mention Berlin?"}]}' | jq
```

The response carries the agent's `answer`, a `trace` of the tool calls
it made, and a `truncated` flag. The turn is logged as a parent
`agent_query` audit row; each tool it calls writes its own row.
## Next steps

- Load real data with the [ingestion pipeline](ingestion.md).
- The full tool set, the graph data model, and the audit contract are in
  [`CLAUDE.md`](../CLAUDE.md); the runtime shape is in
  [architecture.md](architecture.md).
- Audit-log semantics and the §76 BDSG posture: [compliance.md](compliance.md).
