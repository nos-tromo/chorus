# 0009 — Natural-language agent via OpenAI tool-calling

Status: accepted
Date: 2026-05-29

## Context

CLAUDE.md specifies two query surfaces: a structured point-and-click UI for
non-technical users, and an agent-driven natural-language interface for power
users. The four registered retrieval tools (`posts_mentioning`,
`author_activity_summary`, `topic_co_occurrence`, `authors_connected_by_topic`)
gave the structured surface real coverage and, with `GET /tools` already
emitting each tool's JSON schema, a ready-made menu for an agent to orchestrate.
This record covers how the natural-language agent is built.

## Decision

Build the agent as **native OpenAI tool-calling** over the existing tool
registry:

- A bounded loop (`chorus/agent/loop.py::run_agent`) calls the inference
  provider with `tools=` built from `TOOLS` (`chorus/agent/openai_tools.py`),
  using each tool's name, registered `description`, and input-model JSON schema.
- Returned `tool_calls` are validated against the tool's Pydantic input model
  and executed **in-process** via `TOOLS[name].run(driver, parsed, user=, audit=)`,
  so the existing `@audited` wrapper fires per call. Results are fed back as
  `tool` messages and the loop repeats until the model answers.
- **Multi-turn, stateless server**: the client sends the visible conversation;
  the server prepends the system prompt and runs the loop per request
  (`POST /agent/query`).
- The agent can only call the named tools — it cannot emit Cypher. This is
  enforced structurally by tool-calling, not by prompt instruction alone.
- **Audit**: the whole turn is one parent `agent_query` row; each tool call
  writes its own §76 row. The principal from `resolve_principal` is attributed
  on every row.
- The loop is bounded by `AGENT_MAX_ITERATIONS` (default 6). Unknown tools,
  invalid arguments, and tool exceptions are fed back as error messages so the
  model can recover; hitting the cap returns a `truncated` result.

## Alternatives considered

- **Prompted-JSON / ReAct (model-agnostic).** Instruct the model to emit a JSON
  tool request that we parse ourselves. Works on any chat model, including ones
  without native function-calling, but is more brittle (parsing/repair) and
  needs more prompt upkeep. Retained as the documented fallback if the deployed
  model lacks tool-calling.
- **Free-form Cypher generation by the agent.** Rejected: violates the CLAUDE.md
  anti-scope and the §76/auditability posture. High-value queries stay as named,
  version-controlled, parameterized tools; `escape_hatch_cypher` remains behind a
  permission flag and out of the default UI.
- **A separate agent microservice.** Rejected as unnecessary: the agent runs
  in-process in the existing API, reusing the driver/audit/provider seams and
  adding no new airgap surface.

## Consequences

- Positive: natural-language access for power users; structurally bounded to the
  named tools (no Cypher); a complete §76 trail (NL query → tool calls);
  provider-swappable via env; no new services or airgap surface.
- Negative: depends on the served chat model supporting OpenAI function-calling
  through LiteLLM; tool selection is non-deterministic; latency scales with the
  number of tool-call rounds.
- Reversal trigger: if the deployed model cannot do native tool-calling, swap the
  single `provider.chat_message` call in `loop.py` for the prompted-JSON strategy
  — same registry, same audit, same endpoint, a localized change.
