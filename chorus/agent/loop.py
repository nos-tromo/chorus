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

import openai
from loguru import logger
from neo4j import Driver
from pydantic import BaseModel, ValidationError

from chorus.agent.openai_tools import tool_definitions
from chorus.agent.prompts import SYSTEM_PROMPT
from chorus.audit.logger import AuditLogger
from chorus.inference import provider
from chorus.tools import TOOLS


_MAX_TOOL_MESSAGE_ITEMS = 8
_MAX_TOOL_MESSAGE_STRING_CHARS = 280


class AgentInferenceError(RuntimeError):
    """Raised when the agent's inference call fails (unreachable or errored).

    The agent fails loud with a readable message rather than leaking a raw
    500; the router maps this to a 502. Subclassed by
    :class:`ToolCallingUnsupportedError` for the specific capability failure.
    """


class ContextWindowExceededError(AgentInferenceError):
    """Raised when the assembled agent request exceeds the model context window."""


class ToolCallingUnsupportedError(AgentInferenceError):
    """Raised when the inference backend rejects a tool-calling request.

    Signals either that the configured chat model does not support
    function-calling or that the backend is not configured to expose it
    correctly. chorus does not ship a prompted-JSON fallback (see ADR 0009).
    """


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
            try:
                msg = provider.chat_message(convo, model=model, tools=tools, tool_choice="auto")
            except openai.OpenAIError as err:
                if _is_tool_calling_unsupported(err):
                    if _is_vllm_auto_tool_choice_error(err):
                        logger.warning("agent: inference backend rejected automatic tool selection: {}", err)
                    else:
                        logger.warning("agent: chat model rejected tool-calling request: {}", err)
                    raise ToolCallingUnsupportedError(_tool_calling_error_message(err)) from err
                if _is_context_window_exceeded(err):
                    logger.warning("agent: request exceeded chat model context window: {}", err)
                    raise ContextWindowExceededError(
                        "The agent request exceeded the chat model's context window. "
                        "This usually means the visible conversation or tool results were too large for "
                        "the configured model. chorus trims verbose tool payloads before follow-up turns, "
                        "but this request still did not fit. "
                        f"Underlying error: {err}"
                    ) from err
                logger.warning("agent: inference request failed: {}", err)
                raise AgentInferenceError(
                    "The inference backend request failed "
                    f"(provider base URL: {provider.api_base()}). Check that the "
                    "inference service is reachable and INFERENCE_PROVIDER / "
                    f"OPENAI_API_BASE are correct. Underlying error: {err!r}"
                ) from err
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
        _tool_message(tc, result, result_count=result_count),
    )


def _tool_message(tc: Any, content: dict[str, Any], *, result_count: int | None = None) -> dict[str, Any]:
    """Build an OpenAI ``tool`` result message linked to a tool-call id."""
    compact = _compact_tool_content(content, result_count=result_count)
    return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(compact)}


def _compact_tool_content(content: dict[str, Any], *, result_count: int | None = None) -> dict[str, Any]:
    """Return a context-bounded view of ``content`` for the model loop.

    The agent keeps the full tool output for audit and API return values, but
    the follow-up model turn only needs enough structured evidence to answer.
    Large lists and long strings are truncated here so one verbose tool result
    does not exhaust the chat model's context window.
    """
    compacted, truncated = _compact_tool_value(content)
    payload = cast("dict[str, Any]", compacted)
    meta: dict[str, Any] = {}
    if result_count is not None:
        meta["result_count"] = result_count
    if truncated:
        meta["truncated"] = True
        meta["note"] = "Lists and long strings were truncated to fit model context."
    if meta:
        payload = {**payload, "_meta": meta}
    return payload


def _compact_tool_value(value: Any) -> tuple[Any, bool]:
    """Recursively compact ``value`` for inclusion in a tool message."""
    if isinstance(value, str):
        if len(value) <= _MAX_TOOL_MESSAGE_STRING_CHARS:
            return value, False
        trimmed = value[: _MAX_TOOL_MESSAGE_STRING_CHARS - 3].rstrip()
        return f"{trimmed}...", True
    if isinstance(value, list):
        truncated = len(value) > _MAX_TOOL_MESSAGE_ITEMS
        compacted_items: list[Any] = []
        for item in value[:_MAX_TOOL_MESSAGE_ITEMS]:
            compacted_item, item_truncated = _compact_tool_value(item)
            compacted_items.append(compacted_item)
            truncated = truncated or item_truncated
        return compacted_items, truncated
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        truncated = False
        for key, item in value.items():
            compacted_item, item_truncated = _compact_tool_value(item)
            compacted[key] = compacted_item
            truncated = truncated or item_truncated
        return compacted, truncated
    return value, False


def _is_tool_calling_unsupported(err: Exception) -> bool:
    """Heuristically decide whether ``err`` means the model can't do tool-calling.

    Keys off tool/function wording first; a bare 400/422 (but **not** a 404,
    which is typically a missing model or wrong path) that says "not supported"
    also counts. Everything else — connection errors, 404s, 5xx — is left to the
    generic inference-error path so it is not mislabelled a capability failure.
    """
    text = str(err).lower()
    keywords = (
        "tool",
        "function call",
        "function_call",
        "function-calling",
        "enable_auto_tool",
        "enable-auto-tool-choice",
        "tool-call-parser",
    )
    if any(kw in text for kw in keywords):
        return True
    status = getattr(err, "status_code", None)
    return status in (400, 422) and ("not support" in text or "unsupported" in text)


def _is_vllm_auto_tool_choice_error(err: Exception) -> bool:
    """Return ``True`` for vLLM's missing auto-tool-choice server-flag error."""
    text = str(err).lower()
    return "enable-auto-tool-choice" in text and "tool-call-parser" in text


def _tool_calling_error_message(err: Exception) -> str:
    """Build a user-facing explanation for a rejected tool-calling request."""
    if _is_vllm_auto_tool_choice_error(err):
        return (
            "The inference backend rejected automatic tool selection. This is a backend "
            "configuration issue, not necessarily a model capability issue. On vLLM, "
            "start the server with `--enable-auto-tool-choice` and a model-matched "
            "`--tool-call-parser`; the Gemma 4 vLLM recipe also uses "
            "`--reasoning-parser gemma4` and "
            "`--chat-template examples/tool_chat_template_gemma4.jinja`. "
            f"Underlying error: {err}"
        )
    return (
        "The configured chat model rejected the tool-calling request and may not support "
        "function-calling (see ADR 0009). "
        f"Underlying error: {err}"
    )


def _is_context_window_exceeded(err: Exception) -> bool:
    """Return ``True`` when ``err`` indicates the prompt exceeded model context."""
    text = str(err).lower()
    phrases = (
        "contextwindowexceedederror",
        "maximum context length",
        "maximum input length",
        "input_tokens",
        "requested 0 output tokens",
    )
    return any(phrase in text for phrase in phrases)
