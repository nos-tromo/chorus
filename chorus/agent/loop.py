"""The agent tool-calling loop.

Given a user's chat messages, repeatedly asks the model (via the inference
provider) which tool to call, executes the chosen tool against the graph through
the audited registry, feeds the result back, and returns the model's final
answer plus a trace of the tool calls. Bounded by ``max_iterations``.

The whole turn is wrapped in one parent ``agent_query`` audit row; each executed
tool writes its own §76 row via ``@audited``.
"""

from __future__ import annotations

import json
from typing import Any, cast

from neo4j import Driver
from pydantic import BaseModel, ValidationError

from chorus.agent.openai_tools import tool_definitions
from chorus.agent.prompts import SYSTEM_PROMPT
from chorus.audit.logger import AuditLogger
from chorus.inference import provider
from chorus.tools import TOOLS


class TraceStep(BaseModel):
    """One tool call the agent made during a turn.

    Attributes:
        tool: Tool name the model requested.
        arguments: Parsed arguments the model supplied.
        result_count: The tool's reported result count, or ``None`` when the
            call errored or the tool reports no count.
        error: Error message when the call failed (unknown tool, invalid
            arguments, or a tool exception); ``None`` on success.
    """

    tool: str
    arguments: dict[str, Any]
    result_count: int | None = None
    error: str | None = None


class AgentResult(BaseModel):
    """Outcome of an agent turn.

    Attributes:
        answer: The model's final natural-language answer (empty when the
            iteration cap was hit before the model answered).
        trace: The tool calls made, in order.
        truncated: ``True`` when the loop stopped at ``max_iterations`` before
            the model produced an answer.
    """

    answer: str
    trace: list[TraceStep]
    truncated: bool = False


def run_agent(
    driver: Driver,
    audit: AuditLogger,
    *,
    user: str,
    messages: list[dict[str, Any]],
    max_iterations: int = 6,
    model: str | None = None,
) -> AgentResult:
    """Run the tool-calling loop for one chat turn.

    Args:
        driver: Open Neo4j driver, passed to each executed tool.
        audit: Audit logger; the turn is recorded as a parent ``agent_query``
            row and each tool call writes its own row.
        user: Authenticated principal, attributed on every audit row.
        messages: Prior visible conversation turns (role/content); the last
            should be the new user message.
        max_iterations: Maximum model-tool rounds before giving up.
        model: Chat model id override; defaults to the provider's TEXT_MODEL.

    Returns:
        An :class:`AgentResult` with the final answer and the tool-call trace.
    """
    convo: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
    tools = tool_definitions()
    trace: list[TraceStep] = []
    with audit.time_tool(user, "agent_query", {"messages": messages}) as slot:
        for _ in range(max_iterations):
            msg = provider.chat_message(convo, model=model, tools=tools, tool_choice="auto")
            tool_calls = list(msg.tool_calls or [])
            if not tool_calls:
                slot.result_count = len(trace)
                return AgentResult(answer=msg.content or "", trace=trace, truncated=False)
            convo.append(_assistant_turn(msg.content, tool_calls))
            for tc in tool_calls:
                step, tool_message = _execute_tool_call(tc, driver, audit, user=user)
                trace.append(step)
                convo.append(tool_message)
        slot.result_count = len(trace)
        return AgentResult(answer="", trace=trace, truncated=True)


def _assistant_turn(content: str | None, tool_calls: list[Any]) -> dict[str, Any]:
    """Rebuild the assistant tool-call turn in OpenAI message shape."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ],
    }


def _execute_tool_call(tc: Any, driver: Driver, audit: AuditLogger, *, user: str) -> tuple[TraceStep, dict[str, Any]]:
    """Execute one model tool call, returning its trace step and tool message.

    Failures (unknown tool, invalid arguments, tool exception) become an
    ``{"error": ...}`` tool message fed back to the model and a TraceStep with
    ``error`` set; the loop then continues so the model can recover.
    """
    name = tc.function.name
    loaded: Any
    try:
        loaded = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError:
        loaded = {}
    arguments: dict[str, Any] = cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}

    spec = TOOLS.get(name)
    if spec is None:
        error = f"unknown tool: {name}"
        return TraceStep(tool=name, arguments=arguments, error=error), _tool_message(tc, {"error": error})

    try:
        parsed = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        error = f"invalid arguments: {exc.errors()}"
        return TraceStep(tool=name, arguments=arguments, error=error), _tool_message(tc, {"error": error})

    try:
        out = spec.run(driver, parsed, user=user, audit=audit)
    except Exception as exc:  # surface any tool failure back to the model
        error = f"{type(exc).__name__}: {exc}"
        return TraceStep(tool=name, arguments=arguments, error=error), _tool_message(tc, {"error": error})

    result = out.model_dump(mode="json")
    count_fn = getattr(out, "audit_result_count", None)
    result_count = count_fn() if callable(count_fn) else None
    return (
        TraceStep(tool=name, arguments=arguments, result_count=result_count),
        _tool_message(tc, result),
    )


def _tool_message(tc: Any, content: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI ``tool`` result message linked to a tool-call id."""
    return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(content)}
