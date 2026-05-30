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
- **Inference failures are fail-loud, not papered over.** Any failed inference
  call raises `AgentInferenceError` (the tool-calling-unsupported case is the
  subclass `ToolCallingUnsupportedError`); `run_agent` logs a warning and
  `POST /agent/query` returns a `502` with a readable message — an
  unreachable/misconfigured backend reports the provider base URL, and an
  incompatible model reports the likely missing function-calling support. This
  avoids leaking a raw `500` (whose plaintext body the UI can't surface). chorus
  does **not** implement a prompted-JSON fallback (see Alternatives). The
  structured query tools are unaffected — only the natural-language agent is
  unavailable.

## Alternatives considered

- **Prompted-JSON / ReAct (model-agnostic).** Instruct the model to emit a JSON
  tool request that we parse ourselves. Works on any chat model, including ones
  without native function-calling, but is more brittle (parsing/repair), needs
  ongoing prompt upkeep, and yields lower-quality tool use than native
  function-calling where both are available. **Considered but not implemented:**
  chorus targets an operator-controlled inference stack where the chat model is
  chosen to support tool-calling, so an incompatible model is a deployment fix,
  not a runtime case worth a brittle second code path. Revisit only if chorus
  must support uncontrolled or heterogeneous models it does not choose.
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
  through LiteLLM — an incompatible model disables the agent (surfaced as a
  logged warning + a `502`; the structured tools still work). Tool selection is
  non-deterministic; latency scales with the number of tool-call rounds. The
  *silent* case — a model that ignores `tools` and answers anyway — cannot be
  reliably distinguished from a legitimate no-tool answer, so it is not detected
  (documented limitation).
- Reversal trigger: if a deployment must support a non-tool-calling model,
  prefer configuring a compatible model; only if that is impossible, implement
  the prompted-JSON strategy then — a localized change to the single
  `provider.chat_message` call in `loop.py` (same registry, audit, endpoint).
