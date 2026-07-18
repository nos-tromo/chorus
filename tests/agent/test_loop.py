"""Agent tool-calling loop, exercised with a stubbed inference provider.

The provider is monkeypatched with scripted assistant messages so the loop
runs deterministically without reaching the LiteLLM proxy. Tools execute for
real against the migrated test database.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from neo4j import Driver


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, *, content: str | None = None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


def _script(monkeypatch: pytest.MonkeyPatch, responses: list[_FakeMessage]) -> None:
    """Make provider.chat_message return each response in turn."""
    from chorus.inference import provider

    it: Iterator[_FakeMessage] = iter(responses)

    def _fn(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        return next(it)

    monkeypatch.setattr(provider, "chat_message", _fn)


def _always(monkeypatch: pytest.MonkeyPatch, factory: Callable[[], _FakeMessage]) -> None:
    """Make provider.chat_message return a fresh message on every call."""
    from chorus.inference import provider

    def _fn(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        return factory()

    monkeypatch.setattr(provider, "chat_message", _fn)


def _install_fake_tool_run(monkeypatch: pytest.MonkeyPatch, name: str, fn: Callable[..., Any]) -> None:
    """Replace a registered tool's ``run`` callable for the duration of the test."""
    from chorus.tools import TOOLS

    original = TOOLS[name]
    monkeypatch.setitem(TOOLS, name, dataclasses.replace(original, run=fn))


def test_no_tool_call_returns_content(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the model answers without tools, the answer is returned, trace empty."""
    from chorus.agent.loop import run_agent

    _script(monkeypatch, [_FakeMessage(content="I can help with that.")])
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.answer == "I can help with that."
    assert result.trace == []
    assert result.truncated is False


def test_one_tool_call_executes_and_answers(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool_call is executed against the graph, then the model's answer returns."""
    from chorus.agent.loop import run_agent

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (p:Post:Posting {uuid: 'p-1'})
              ON CREATE SET p.text = 'hello berlin',
                            p.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(al)
            """
        )
    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "posts_mentioning", '{"entity": "Berlin", "limit": 5}')]),
            _FakeMessage(content="Found 1 post mentioning Berlin."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="analyst",
        messages=[{"role": "user", "content": "posts about Berlin?"}],
    )
    assert result.answer == "Found 1 post mentioning Berlin."
    assert len(result.trace) == 1
    assert result.trace[0].tool == "posts_mentioning"
    assert result.trace[0].result_count == 1
    assert result.trace[0].error is None

    tool_names = [
        row[0]
        for row in sqlite3.connect(in_memory_audit.db_path)
        .execute("SELECT tool_name FROM audit_log ORDER BY id")
        .fetchall()
    ]
    assert "posts_mentioning" in tool_names  # child row
    assert "agent_query" in tool_names  # parent row


def test_max_iterations_truncates(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the model never stops calling tools, the loop stops and flags truncated."""
    from chorus.agent.loop import run_agent

    _always(
        monkeypatch,
        lambda: _FakeMessage(tool_calls=[_FakeToolCall("c", "posts_mentioning", '{"entity": "x"}')]),
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "loop forever"}],
        max_iterations=3,
    )
    assert result.truncated is True
    assert result.answer == ""
    assert len(result.trace) == 3


def test_unknown_tool_records_error_and_continues(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown tool name is fed back as an error; the loop recovers."""
    from chorus.agent.loop import run_agent

    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "does_not_exist", "{}")]),
            _FakeMessage(content="Sorry, I can't do that."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "do the impossible"}],
    )
    assert result.answer == "Sorry, I can't do that."
    assert len(result.trace) == 1
    assert result.trace[0].tool == "does_not_exist"
    assert result.trace[0].error is not None


def test_invalid_arguments_records_error(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema-violating tool arguments are fed back as an error; the loop recovers."""
    from chorus.agent.loop import run_agent

    _script(
        monkeypatch,
        [
            # posts_mentioning requires `entity`; omit it and send a bad limit.
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "posts_mentioning", '{"limit": "NaN"}')]),
            _FakeMessage(content="Let me try differently."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "bad args"}],
    )
    assert result.answer == "Let me try differently."
    assert len(result.trace) == 1
    assert result.trace[0].error is not None


def test_tool_calling_unsupported_is_raised(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend that rejects the tools request surfaces ToolCallingUnsupportedError."""
    import openai

    from chorus.agent.loop import ToolCallingUnsupportedError, run_agent
    from chorus.inference import provider

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise openai.OpenAIError("this model does not support tools")

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(ToolCallingUnsupportedError):
        run_agent(
            migrated_driver,
            in_memory_audit,
            user="u",
            messages=[{"role": "user", "content": "hi"}],
        )


def test_vllm_auto_tool_choice_error_mentions_backend_flags(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vLLM auto-tool-choice rejection is reported as a backend config issue."""
    import openai

    from chorus.agent.loop import ToolCallingUnsupportedError, run_agent
    from chorus.inference import provider

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise openai.OpenAIError(
            'OpenAIException - "auto" tool choice requires --enable-auto-tool-choice '
            "and --tool-call-parser to be set. Received Model Group=google/gemma-4-E2B-it"
        )

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(ToolCallingUnsupportedError) as excinfo:
        run_agent(
            migrated_driver,
            in_memory_audit,
            user="u",
            messages=[{"role": "user", "content": "hi"}],
        )
    detail = str(excinfo.value)
    assert "backend configuration issue" in detail
    assert "--enable-auto-tool-choice" in detail
    assert "--tool-call-parser" in detail


def test_tool_message_compacts_large_payload() -> None:
    """Large tool payloads are compacted before being fed back to the model."""
    from chorus.agent.loop import _tool_message

    tc = _FakeToolCall("c1", "posts_mentioning", '{"entity": "Deutschland"}')
    content = {
        "hits": [
            {
                "uuid": f"p-{index}",
                "text": "Deutschland " * 80,
                "ts": "2026-05-01T10:00:00+00:00",
                "labels": ["Post", "Posting"],
                "entity_id": None,
                "matched_name": "Deutschland",
            }
            for index in range(20)
        ]
    }

    message = _tool_message(tc, content, result_count=20)
    payload = json.loads(message["content"])

    assert len(payload["hits"]) == 8
    assert payload["hits"][0]["text"].endswith("...")
    assert payload["_meta"]["result_count"] == 20
    assert payload["_meta"]["truncated"] is True


def test_tool_message_respects_custom_compaction_limits() -> None:
    """Compaction caps are parameters, not hard-coded module constants."""
    from chorus.agent.loop import _tool_message

    tc = _FakeToolCall("c1", "posts_mentioning", "{}")
    content = {"items": ["one", "two", "three", "four"], "blurb": "x" * 40}

    message = _tool_message(tc, content, max_items=2, max_chars=5)
    payload = json.loads(message["content"])

    assert len(payload["items"]) == 2
    assert payload["blurb"] == "xx..."


def test_compaction_note_states_the_caps() -> None:
    """The truncation note names the caps so the model treats lists as samples."""
    from chorus.agent.loop import _tool_message

    tc = _FakeToolCall("c1", "posts_mentioning", "{}")
    content = {"items": list(range(50))}

    message = _tool_message(tc, content)
    note = json.loads(message["content"])["_meta"]["note"]

    assert "8" in note
    assert "280" in note


def test_compaction_preserves_tool_supplied_meta() -> None:
    """A tool's own ``_meta`` is merged, never clobbered by the envelope."""
    from chorus.agent.loop import _tool_message

    tc = _FakeToolCall("c1", "posts_mentioning", "{}")
    content = {"_meta": {"tool_field": 1}, "rows": [1, 2, 3]}

    message = _tool_message(tc, content, result_count=3)
    meta = json.loads(message["content"])["_meta"]

    assert meta["tool_field"] == 1
    assert meta["result_count"] == 3


def test_tool_message_keeps_non_ascii_unescaped() -> None:
    r"""Non-ASCII tool content reaches the model as real UTF-8, not ``\uXXXX``.

    ``json.dumps`` defaults to ``ensure_ascii=True``, which turns an Arabic
    entity name such as ``محمد`` into ``\uXXXX`` escape text in the message.
    A weaker chat model copies those escapes verbatim into its prose, so the agent's
    answer renders literal ``\uXXXX`` instead of Arabic. The serialized tool
    message must carry the real characters so the model reproduces them, while
    still round-tripping to the same structure.
    """
    from chorus.agent.loop import _tool_message

    name = "محمد"
    text = "مرحبا بالعالم"
    tc = _FakeToolCall("c1", "posts_mentioning", '{"entity": "محمد"}')
    content = {"hits": [{"matched_name": name, "text": text}]}

    message = _tool_message(tc, content)

    assert name in message["content"]
    assert text in message["content"]
    assert "\\u" not in message["content"]
    assert json.loads(message["content"])["hits"][0]["matched_name"] == name


def test_context_window_error_is_raised(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend context-window overflow surfaces a dedicated agent error."""
    import openai

    from chorus.agent.loop import ContextWindowExceededError, run_agent
    from chorus.inference import provider

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise openai.OpenAIError(
            "ContextWindowExceededError: This model's maximum context length is 16384 tokens "
            "and your prompt contains at least 16385 input tokens (parameter=input_tokens)"
        )

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(ContextWindowExceededError) as excinfo:
        run_agent(
            migrated_driver,
            in_memory_audit,
            user="u",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert "context window" in str(excinfo.value).lower()


def test_context_window_error_mentioning_tools_is_not_misclassified(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A context overflow whose text also mentions tools is a context error.

    The backend's token-count breakdown can reference the tool results that
    filled the window. Because ``_is_tool_calling_unsupported`` keys off the
    bare word ``tool``, the context-window check must take precedence so such
    an error is not mislabelled a capability failure.
    """
    import openai

    from chorus.agent.loop import ContextWindowExceededError, run_agent
    from chorus.inference import provider

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise openai.OpenAIError(
            "ContextWindowExceededError: This model's maximum context length is 8192 tokens. "
            "However, your messages resulted in 9001 tokens (including tool results). "
            "Please reduce the length of the messages."
        )

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(ContextWindowExceededError) as excinfo:
        run_agent(
            migrated_driver,
            in_memory_audit,
            user="u",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert "context window" in str(excinfo.value).lower()


def test_inference_error_raises_agent_inference_error(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection/other inference failure surfaces as AgentInferenceError, not tool-unsupported."""
    import openai

    from chorus.agent.loop import AgentInferenceError, ToolCallingUnsupportedError, run_agent
    from chorus.inference import provider

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise openai.OpenAIError("connection error")

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(AgentInferenceError) as excinfo:
        run_agent(
            migrated_driver,
            in_memory_audit,
            user="u",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert not isinstance(excinfo.value, ToolCallingUnsupportedError)


def test_model_not_found_is_inference_error_not_unsupported(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 (e.g. wrong model tag) is a generic inference error, not tool-unsupported."""
    import openai

    from chorus.agent.loop import AgentInferenceError, ToolCallingUnsupportedError, run_agent
    from chorus.inference import provider

    class _NotFound(openai.OpenAIError):
        status_code = 404

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise _NotFound("model 'gpt-oss:20b' not found")

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(AgentInferenceError) as excinfo:
        run_agent(
            migrated_driver,
            in_memory_audit,
            user="u",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert not isinstance(excinfo.value, ToolCallingUnsupportedError)


def test_language_selects_system_prompt(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_agent(language='de') sends the German system prompt to the provider."""
    from chorus.agent.loop import run_agent
    from chorus.agent.prompts import get_system_prompt
    from chorus.inference import provider

    captured: list[dict[str, Any]] = []

    def _capture(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        captured.append(messages[0])
        return _FakeMessage(content="ok")

    monkeypatch.setattr(provider, "chat_message", _capture)
    run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "hallo"}],
        language="de",
    )
    assert captured[0]["role"] == "system"
    assert captured[0]["content"] == get_system_prompt("de")


def test_default_language_is_english_prompt(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no language argument, the English prompt is used."""
    from chorus.agent.loop import run_agent
    from chorus.agent.prompts import get_system_prompt
    from chorus.inference import provider

    captured: list[dict[str, Any]] = []

    def _capture(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        captured.append(messages[0])
        return _FakeMessage(content="ok")

    monkeypatch.setattr(provider, "chat_message", _capture)
    run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert captured[0]["content"] == get_system_prompt("en")


def test_trace_carries_graph_tool_result(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph tool's full node/edge payload rides on the trace, uncompacted."""
    from chorus.agent.loop import run_agent
    from chorus.tools.network_around import NetworkAroundOut, NetworkEdge, NetworkNode

    fake_out = NetworkAroundOut(
        seed="Berlin",
        seed_node_id="topic:berlin",
        nodes=[
            NetworkNode(id="topic:berlin", kind="topic", label="Berlin", entity_id=None, is_seed=True),
            NetworkNode(id="author:a1", kind="author", label="alice", entity_id=None, is_seed=False),
        ],
        edges=[NetworkEdge(source="author:a1", target="topic:berlin", weight=1)],
        truncated=False,
    )
    _install_fake_tool_run(monkeypatch, "network_around", lambda driver, params, *, user, audit: fake_out)
    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "network_around", '{"entity": "Berlin"}')]),
            _FakeMessage(content="Here is the network."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "show network around Berlin"}],
    )
    assert len(result.trace) == 1
    assert result.trace[0].tool == "network_around"
    assert result.trace[0].result == fake_out.model_dump(mode="json")
    assert result.trace[0].result_count == 2


def test_trace_omits_result_for_non_graph_tools(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-graph tool (e.g. posts_mentioning) never populates trace.result."""
    from chorus.agent.loop import run_agent

    with migrated_driver.session() as s:
        s.run(
            """
            MERGE (al:Alias {surface_form: 'Berlin'})
            MERGE (p:Post:Posting {uuid: 'p-1'})
              ON CREATE SET p.text = 'hello berlin',
                            p.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p)-[:MENTIONS]->(al)
            """
        )
    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "posts_mentioning", '{"entity": "Berlin", "limit": 5}')]),
            _FakeMessage(content="Found 1 post mentioning Berlin."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="analyst",
        messages=[{"role": "user", "content": "posts about Berlin?"}],
    )
    assert len(result.trace) == 1
    assert result.trace[0].tool == "posts_mentioning"
    assert result.trace[0].result_count == 1
    assert result.trace[0].result is None


def test_trace_omits_oversized_graph_result(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph tool result over the node cap is withheld, but result_count still reports the true size."""
    from chorus.agent.loop import run_agent
    from chorus.tools.network_around import NetworkAroundOut, NetworkNode

    nodes = [
        NetworkNode(id=f"author:a{i}", kind="author", label=f"a{i}", entity_id=None, is_seed=False) for i in range(501)
    ]
    fake_out = NetworkAroundOut(seed="Berlin", seed_node_id="topic:berlin", nodes=nodes, edges=[], truncated=True)
    _install_fake_tool_run(monkeypatch, "network_around", lambda driver, params, *, user, audit: fake_out)
    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "network_around", '{"entity": "Berlin"}')]),
            _FakeMessage(content="Large network."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "show network around Berlin"}],
    )
    assert len(result.trace) == 1
    assert result.trace[0].result is None
    assert result.trace[0].result_count == 501


def test_trace_result_none_on_tool_error(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed tool call (unknown tool) leaves trace.result None alongside the error."""
    from chorus.agent.loop import run_agent

    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "does_not_exist", "{}")]),
            _FakeMessage(content="Sorry, I can't do that."),
        ],
    )
    result = run_agent(
        migrated_driver,
        in_memory_audit,
        user="u",
        messages=[{"role": "user", "content": "do the impossible"}],
    )
    assert len(result.trace) == 1
    assert result.trace[0].error is not None
    assert result.trace[0].result is None
