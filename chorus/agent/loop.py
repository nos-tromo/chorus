"""The agent tool-calling loop.

Given a user's chat messages, repeatedly asks the model (via the inference
provider) which tool to call, executes the chosen tool against the graph through
the audited registry, feeds the result back, and returns the model's final
answer plus a trace of the tool calls. Bounded by ``max_iterations``.

Audit trail: each executed tool writes its own ``query`` event via
``@audited`` as it completes. The turn ends with one ``ai_call`` /
``agent.answer`` event — the record of what went to the chat model
(``recipient="inference:chat"``): the model alias, the token counts when
reported, the ids of this turn's tool events as ``sources`` (the evidence
the model was given), the answer's SHA-256 and a SHA-256 of the question.
No question or answer text is recorded. A turn that fails at the model
writes that event with ``outcome="error"``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

import openai
from loguru import logger
from neo4j import Driver
from pydantic import BaseModel, ValidationError

from chorus.agent.openai_tools import tool_definitions
from chorus.agent.prompts import get_system_prompt
from chorus.audit.trail import AuditContext, AuditFailure, PurposeRequired
from chorus.inference import provider
from chorus.tools import TOOLS
from chorus.utils.audit_event import AiCall, sha256_text
from chorus.utils.log_redaction import redact_validation_errors

#: The audit operation of a whole agent turn.
AGENT_OPERATION = "agent.answer"
#: The actions an agent turn performs: tool queries, and the chat-model call
#: that carries the conversation and the tool results.
AGENT_ACTIONS = ("query", "ai_call")

_MAX_TOOL_MESSAGE_ITEMS = 8
_MAX_TOOL_MESSAGE_STRING_CHARS = 280

# Graph tools whose full node/edge payload is echoed on the trace so the SPA
# can render the graph inline (wiring (a) of the reactive-graph design). The
# node cap keeps one huge graph from ballooning the /agent/query response;
# result_count still reports the true size when the payload is withheld.
_GRAPH_RESULT_TOOLS = frozenset(
    {"network_around", "social_network_around", "expand_network_node", "expand_social_node"}
)
_MAX_TRACE_GRAPH_NODES = 500


class AgentInferenceError(RuntimeError):
    """Raised when the agent's inference call fails (unreachable or errored).

    The agent fails loud with a readable message rather than leaking a raw
    500; the router maps this to a 502. Subclassed by
    :class:`ToolCallingUnsupportedError` (the model or backend cannot do
    tool-calling) and :class:`ContextWindowExceededError` (the request was
    too large for the model) for the two specific, identifiable failures.
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
        error: Static, client-safe message when the call failed (unknown
            tool, invalid arguments, or a tool exception); ``None`` on
            success. The underlying detail is logged, not returned here.
        result: Full tool output for graph tools (node/edge payloads the SPA
            renders inline), when within the size cap; None otherwise.
    """

    tool: str
    arguments: dict[str, Any]
    result_count: int | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


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
    audit: AuditContext,
    *,
    messages: list[dict[str, Any]],
    max_iterations: int = 6,
    model: str | None = None,
    language: str = "en",
    tool_message_max_items: int = _MAX_TOOL_MESSAGE_ITEMS,
    tool_message_max_chars: int = _MAX_TOOL_MESSAGE_STRING_CHARS,
) -> AgentResult:
    """Run the tool-calling loop for one chat turn.

    Args:
        driver: Open Neo4j driver, passed to each executed tool.
        audit: The caller's audit context. Each tool call writes its own
            ``query`` event; the turn writes one ``ai_call`` /
            ``agent.answer`` event at the end.
        messages: Prior visible conversation turns (role/content); the last
            should be the new user message.
        max_iterations: Maximum model-tool rounds before giving up.
        model: Chat model id override; defaults to the provider's TEXT_MODEL.
        language: Response-language code (``en``/``de``) selecting the system
            prompt; defaults to English.
        tool_message_max_items: Maximum list items kept per tool result fed
            back to the model; longer lists are truncated.
        tool_message_max_chars: Maximum characters kept per string in a tool
            result fed back to the model; longer strings are truncated.

    Returns:
        An :class:`AgentResult` with the final answer and the tool-call trace.

    Raises:
        PurposeRequired: If the turn's actions need a stated purpose and the
            caller stated none (checked before anything reaches the model).
        AgentInferenceError: If the chat-model call fails.
        AuditFailure: If an audit event could not be written.
    """
    question = _last_user_text(messages)
    criteria: dict[str, object] = {"messages": len(messages), "max_iterations": max_iterations}
    if question:
        criteria["question_sha256"] = sha256_text(question)
    audit.require_purpose(AGENT_ACTIONS, AGENT_OPERATION, criteria)

    system_prompt = get_system_prompt(language)
    convo: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *messages]
    tools = tool_definitions()
    trace: list[TraceStep] = []
    turn = audit.scoped()  # collects the ids of this turn's tool events
    usage = provider.TokenUsage()
    ai = AiCall(
        model_alias=model or provider.text_model(),
        prompt_version=f"agent-{language}-{sha256_text(system_prompt)[:12]}",
    )
    try:
        for _ in range(max_iterations):
            msg = _chat(convo, model=model, tools=tools, usage=usage)
            tool_calls = list(msg.tool_calls or [])
            if not tool_calls:
                answer = msg.content or ""
                _record_turn(audit, turn, ai, usage, criteria, answer=answer, trace=trace, truncated=False)
                return AgentResult(answer=answer, trace=trace, truncated=False)
            convo.append(_assistant_turn(msg.content, tool_calls))
            for tc in tool_calls:
                step, tool_message = _execute_tool_call(
                    tc,
                    driver,
                    turn,
                    max_items=tool_message_max_items,
                    max_chars=tool_message_max_chars,
                )
                trace.append(step)
                convo.append(tool_message)
        _record_turn(audit, turn, ai, usage, criteria, answer="", trace=trace, truncated=True)
        return AgentResult(answer="", trace=trace, truncated=True)
    except (AuditFailure, PurposeRequired):
        raise
    except Exception:
        _record_turn(audit, turn, ai, usage, criteria, answer="", trace=trace, truncated=False, outcome="error")
        raise


def _chat(
    convo: list[dict[str, Any]],
    *,
    model: str | None,
    tools: list[dict[str, Any]],
    usage: provider.TokenUsage,
) -> Any:
    """Ask the chat model for the next assistant message, mapping its failures.

    Args:
        convo: The conversation so far, system prompt first.
        model: Chat model id override, or ``None`` for ``TEXT_MODEL``.
        tools: The OpenAI tool definitions to advertise.
        usage: Accumulator for the response's token usage.

    Returns:
        The assistant message (content and/or tool calls).

    Raises:
        ContextWindowExceededError: If the request was too large for the model.
        ToolCallingUnsupportedError: If the model or backend rejects tool calling.
        AgentInferenceError: For any other inference failure.
    """
    try:
        return provider.chat_message(convo, model=model, tools=tools, tool_choice="auto", usage=usage)
    except openai.OpenAIError as err:
        # Context-window overflow is unambiguous and is checked first: its
        # token-count text can mention "tool" results, which would otherwise
        # trip the broad keyword match in `_is_tool_calling_unsupported`.
        if _is_context_window_exceeded(err):
            logger.warning("agent: request exceeded chat model context window: {}", err)
            raise ContextWindowExceededError(
                "The agent request exceeded the chat model's context window. "
                "This usually means the visible conversation or tool results were too large for "
                "the configured model. chorus trims verbose tool payloads before follow-up turns, "
                "but this request still did not fit. "
                f"Underlying error: {err}"
            ) from err
        if _is_tool_calling_unsupported(err):
            if _is_vllm_auto_tool_choice_error(err):
                logger.warning("agent: inference backend rejected automatic tool selection: {}", err)
            else:
                logger.warning("agent: chat model rejected tool-calling request: {}", err)
            raise ToolCallingUnsupportedError(_tool_calling_error_message(err)) from err
        logger.warning("agent: inference request failed: {}", err)
        raise AgentInferenceError(
            "The inference backend request failed "
            f"(provider base URL: {provider.api_base()}). Check that the "
            "inference service is reachable and INFERENCE_PROVIDER / "
            f"OPENAI_API_BASE are correct. Underlying error: {err!r}"
        ) from err


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """Return the text of the conversation's last user turn.

    Args:
        messages: The visible conversation, oldest first.

    Returns:
        The last ``user`` message's content, or ``""`` when there is none.
    """
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _record_turn(
    audit: AuditContext,
    turn: AuditContext,
    ai: AiCall,
    usage: provider.TokenUsage,
    criteria: Mapping[str, object],
    *,
    answer: str,
    trace: list[TraceStep],
    truncated: bool,
    outcome: str = "ok",
) -> str:
    """Write the turn's ``ai_call`` / ``agent.answer`` event.

    Args:
        audit: The caller's audit context.
        turn: The turn's scoped context, holding its tool events' ids.
        ai: The turn's model provenance (alias and prompt version).
        usage: Token counts over the turn's model calls.
        criteria: The turn's parameters (message count, question hash).
        answer: The answer shown to the caller; only its hash is recorded.
        trace: The tool calls the model made.
        truncated: Whether the turn hit the iteration cap.
        outcome: ``ok`` or ``error``.

    Returns:
        The event's id.

    Raises:
        AuditFailure: If the event could not be written.
    """
    return audit.emit(
        "ai_call",
        AGENT_OPERATION,
        outcome=outcome,
        recipient="inference:chat",
        criteria={**criteria, "tool_calls": len(trace), "truncated": truncated},
        ai=AiCall(
            model_alias=ai.model_alias,
            prompt_version=ai.prompt_version,
            sources=list(turn.event_ids),
            output_sha256=sha256_text(answer) if answer else None,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        ),
    )


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


def _execute_tool_call(
    tc: Any,
    driver: Driver,
    audit: AuditContext,
    *,
    max_items: int = _MAX_TOOL_MESSAGE_ITEMS,
    max_chars: int = _MAX_TOOL_MESSAGE_STRING_CHARS,
) -> tuple[TraceStep, dict[str, Any]]:
    """Execute one model tool call, returning its trace step and tool message.

    Failures (unknown tool, invalid arguments, tool exception) become an
    ``{"error": ...}`` tool message fed back to the model (full detail, so it
    can self-correct) and a TraceStep with a static, client-safe ``error``
    set; the loop then continues so the model can recover. The failure is
    logged in every case; invalid arguments are logged without their values.
    An audit failure or a refused purpose is never treated as a tool
    failure: it ends the turn.
    """
    name = tc.function.name
    loaded: Any
    try:
        loaded = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError:
        loaded = {}  # pyrefly: ignore[implicit-any-empty-container]  # fallback dict when JSON is unparseable
    arguments: dict[str, Any] = cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}

    spec = TOOLS.get(name)
    if spec is None:
        detail = f"unknown tool: {name}"
        logger.warning(f"agent: {detail}")
        return TraceStep(tool=name, arguments=arguments, error="Tool call failed."), _tool_message(
            tc, {"error": detail}, max_items=max_items, max_chars=max_chars
        )

    try:
        parsed = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        detail = f"invalid arguments: {exc.errors()}"
        logger.warning("agent: invalid arguments for tool {!r}: {}", name, redact_validation_errors(exc.errors()))
        return TraceStep(tool=name, arguments=arguments, error="Tool call failed."), _tool_message(
            tc, {"error": detail}, max_items=max_items, max_chars=max_chars
        )

    try:
        out = spec.run(driver, parsed, audit=audit)
    except (AuditFailure, PurposeRequired):
        raise
    except Exception as exc:  # surface any tool failure back to the model
        detail = f"{type(exc).__name__}: {exc}"
        logger.opt(exception=exc).error(f"agent: tool {name!r} failed")
        return TraceStep(tool=name, arguments=arguments, error="Tool call failed."), _tool_message(
            tc, {"error": detail}, max_items=max_items, max_chars=max_chars
        )

    result = out.model_dump(mode="json")
    count_fn = getattr(out, "audit_result_count", None)
    result_count: int | None = cast("int | None", count_fn()) if callable(count_fn) else None
    trace_result: dict[str, Any] | None = None
    if name in _GRAPH_RESULT_TOOLS:
        nodes = result.get("nodes")
        if isinstance(nodes, list) and len(nodes) <= _MAX_TRACE_GRAPH_NODES:
            trace_result = result
    return (
        TraceStep(tool=name, arguments=arguments, result_count=result_count, result=trace_result),
        _tool_message(tc, result, result_count=result_count, max_items=max_items, max_chars=max_chars),
    )


def _tool_message(
    tc: Any,
    content: dict[str, Any],
    *,
    result_count: int | None = None,
    max_items: int = _MAX_TOOL_MESSAGE_ITEMS,
    max_chars: int = _MAX_TOOL_MESSAGE_STRING_CHARS,
) -> dict[str, Any]:
    """Build an OpenAI ``tool`` result message linked to a tool-call id."""
    compact = _compact_tool_content(content, result_count=result_count, max_items=max_items, max_chars=max_chars)
    # ``ensure_ascii=False`` keeps non-ASCII (e.g. Arabic entity names) as real
    # UTF-8 in the tool message rather than ``\uXXXX`` escapes. Otherwise a
    # weaker chat model copies the escape text verbatim into its answer, which
    # the UI then renders as literal ``\uXXXX`` instead of the script. The
    # message is transported as UTF-8 JSON over HTTP, so this is wire-safe.
    return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(compact, ensure_ascii=False)}


def _compact_tool_content(
    content: dict[str, Any],
    *,
    result_count: int | None = None,
    max_items: int = _MAX_TOOL_MESSAGE_ITEMS,
    max_chars: int = _MAX_TOOL_MESSAGE_STRING_CHARS,
) -> dict[str, Any]:
    """Return a context-bounded view of ``content`` for the model loop.

    The agent keeps the full tool output for audit and API return values, but
    the follow-up model turn only needs enough structured evidence to answer.
    Large lists and long strings are truncated here so one verbose tool result
    does not exhaust the chat model's context window.
    """
    compacted, truncated = _compact_tool_value(content, max_items=max_items, max_chars=max_chars)
    payload = cast("dict[str, Any]", compacted)
    meta: dict[str, Any] = {}
    if result_count is not None:
        meta["result_count"] = result_count
    if truncated:
        meta["truncated"] = True
        meta["note"] = (
            f"Truncated to fit model context: each list shows at most {max_items} items "
            f"and each string at most {max_chars} characters. Treat the items shown as a "
            "sample, not the full set; result_count (when present) is the true total."
        )
    if meta:
        # `model_dump` never emits underscore-prefixed keys, so a real tool output
        # cannot carry `_meta`; guard anyway since this helper takes any dict. A
        # caller-supplied `_meta` dict is merged into (its keys win) rather than
        # clobbered; a non-dict `_meta` is left untouched and the envelope dropped.
        existing = payload.get("_meta")
        if isinstance(existing, dict):
            payload = {**payload, "_meta": {**meta, **existing}}
        elif "_meta" not in payload:
            payload = {**payload, "_meta": meta}
    return payload


def _compact_tool_value(
    value: Any, *, max_items: int = _MAX_TOOL_MESSAGE_ITEMS, max_chars: int = _MAX_TOOL_MESSAGE_STRING_CHARS
) -> tuple[Any, bool]:
    """Recursively compact ``value`` for inclusion in a tool message."""
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, False
        trimmed = value[: max_chars - 3].rstrip()
        return f"{trimmed}...", True
    if isinstance(value, list):
        truncated = len(value) > max_items
        compacted_items: list[Any] = []
        for item in value[:max_items]:
            compacted_item, item_truncated = _compact_tool_value(item, max_items=max_items, max_chars=max_chars)
            compacted_items.append(compacted_item)
            truncated = truncated or item_truncated
        return compacted_items, truncated
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        truncated = False
        for key, item in value.items():
            compacted_item, item_truncated = _compact_tool_value(item, max_items=max_items, max_chars=max_chars)
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
