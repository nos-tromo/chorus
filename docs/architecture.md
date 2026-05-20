# chorus architecture

See [`CLAUDE.md`](../CLAUDE.md) for the canonical design. This file accrues
operational details (deployment topology, data-plane contract, runtime
diagrams) as they stabilize.

## Data-plane integration contract

chorus expects the data-plane Compose project to publish a Neo4j service on
`inference-net`:

- service name: `neo4j-chorus`
- bolt port: `7687`
- HTTP port: `7474`

chorus reads `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
from its env. The chorus repo does not declare any persistent volumes; all
graph state lives in the data-plane project's named volumes.

## Inference contract

All inference (chat, embed, rerank, NER) is reached through vllm-service's
LiteLLM proxy at `http://vllm-router:4000/v1`, OpenAI-protocol HTTP,
selected by the `model` field in each request.
