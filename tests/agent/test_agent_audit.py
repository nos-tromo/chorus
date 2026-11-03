"""Audit events of an agent turn: tool queries, the ai_call record, refusals and failures.

The provider is scripted (no inference service); tools run for real against
the migrated test database. All values are invented.
"""

from __future__ import annotations

import atexit
import dataclasses
import io
from collections.abc import Iterator
from typing import Any

import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Driver

from chorus.api.errors import install_error_handlers
from chorus.audit.trail import AuditContext, AuditFailure, PurposeRequired
from chorus.utils.audit_event import Actor, sha256_text
from tests._audit import assert_chain_ok, operation_events, started_trail


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


@dataclasses.dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


def _script(monkeypatch: pytest.MonkeyPatch, responses: list[_FakeMessage], usage: _Usage | None = None) -> list[int]:
    """Make provider.chat_message return each response in turn; return a call counter."""
    from chorus.inference import provider

    it: Iterator[_FakeMessage] = iter(responses)
    calls: list[int] = []

    def _fn(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        calls.append(1)
        sink = kwargs.get("usage")
        if usage is not None and sink is not None:
            sink.add(usage)
        return next(it)

    monkeypatch.setattr(provider, "chat_message", _fn)
    return calls


_QUESTION = [{"role": "user", "content": "Which posts mention Example Town?"}]


def test_turn_records_token_counts_and_prompt_version(
    migrated_driver: Driver, audit_ctx: AuditContext, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reported usage is summed over the turn's model calls; the prompt is identified by hash."""
    from chorus.agent.loop import run_agent

    _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "posts_mentioning", '{"entity": "Example Town"}')]),
            _FakeMessage(content="None found."),
        ],
        usage=_Usage(prompt_tokens=100, completion_tokens=7),
    )
    run_agent(migrated_driver, audit_ctx, messages=_QUESTION, model="example-chat-model")

    tool_event, turn_event = operation_events(audit_stream)
    assert tool_event["operation"] == "tool.posts_mentioning"
    ai = turn_event["ai"]
    assert ai["model_alias"] == "example-chat-model"
    assert ai["input_tokens"] == 200
    assert ai["output_tokens"] == 14
    assert ai["prompt_version"].startswith("agent-en-")
    assert ai["sources"] == [tool_event["event_id"]]


def test_turn_without_tool_calls_has_no_sources(
    migrated_driver: Driver, audit_ctx: AuditContext, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct answer is still one ai_call event; it cites no tool events and no token counts."""
    from chorus.agent.loop import run_agent

    _script(monkeypatch, [_FakeMessage(content="Hello.")])
    run_agent(migrated_driver, audit_ctx, messages=_QUESTION)

    [turn_event] = operation_events(audit_stream)
    assert (turn_event["action"], turn_event["operation"], turn_event["outcome"]) == ("ai_call", "agent.answer", "ok")
    assert "sources" not in turn_event["ai"]
    assert "input_tokens" not in turn_event["ai"]
    assert turn_event["ai"]["output_sha256"] == sha256_text("Hello.")
    assert turn_event["actor"] == {"user": "test-user", "via": "system"}


def test_truncated_turn_records_no_output_hash(
    migrated_driver: Driver, audit_ctx: AuditContext, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn that hits the iteration cap shows no answer, so no output hash is recorded."""
    from chorus.agent.loop import run_agent

    call = _FakeToolCall("c", "posts_mentioning", '{"entity": "Example Town"}')
    _script(monkeypatch, [_FakeMessage(tool_calls=[call]), _FakeMessage(tool_calls=[call])])
    result = run_agent(migrated_driver, audit_ctx, messages=_QUESTION, max_iterations=2)
    assert result.truncated is True

    events = operation_events(audit_stream)
    assert [e["operation"] for e in events] == ["tool.posts_mentioning", "tool.posts_mentioning", "agent.answer"]
    turn_event = events[-1]
    assert turn_event["criteria"]["truncated"] is True
    assert "output_sha256" not in turn_event["ai"]
    assert turn_event["ai"]["sources"] == [events[0]["event_id"], events[1]["event_id"]]


def test_inference_failure_records_an_error_turn(
    migrated_driver: Driver, audit_ctx: AuditContext, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the model call fails, the turn's ai_call event is written with outcome error."""
    from chorus.agent.loop import AgentInferenceError, run_agent
    from chorus.inference import provider

    def _boom(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise openai.OpenAIError("connection error")

    monkeypatch.setattr(provider, "chat_message", _boom)
    with pytest.raises(AgentInferenceError):
        run_agent(migrated_driver, audit_ctx, messages=_QUESTION)

    [turn_event] = operation_events(audit_stream)
    assert (turn_event["action"], turn_event["operation"], turn_event["outcome"]) == (
        "ai_call",
        "agent.answer",
        "error",
    )
    assert turn_event["recipient"] == "inference:chat"
    assert "output_sha256" not in turn_event["ai"]
    assert "connection error" not in audit_stream.getvalue()


def test_missing_purpose_refuses_before_the_model_is_called(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With query required and no purpose, nothing reaches the model and one denied event is written."""
    from chorus.agent.loop import run_agent

    calls = _script(monkeypatch, [_FakeMessage(content="unused")])
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query"}))
    with pytest.raises(PurposeRequired):
        run_agent(migrated_driver, trail.context(Actor(user="alice-example", via="edge")), messages=_QUESTION)
    trail.close()

    assert calls == []
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "agent.answer", "denied")
    assert event["criteria"]["question_sha256"] == sha256_text(_QUESTION[0]["content"])
    assert "target" not in event
    assert_chain_ok(stream)


def test_tool_audit_failure_ends_the_turn(
    migrated_driver: Driver, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An audit failure inside a tool call is not fed back to the model as a tool error."""
    from chorus.agent.loop import run_agent
    from chorus.tools import TOOLS

    def _fails(*_a: Any, **_kw: Any) -> Any:
        raise AuditFailure("an audit event could not be written")

    monkeypatch.setitem(TOOLS, "posts_mentioning", dataclasses.replace(TOOLS["posts_mentioning"], run=_fails))
    calls = _script(
        monkeypatch,
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "posts_mentioning", '{"entity": "Example Town"}')]),
            _FakeMessage(content="unused"),
        ],
    )
    with pytest.raises(AuditFailure):
        run_agent(migrated_driver, audit_ctx, messages=_QUESTION)
    assert calls == [1]  # the model never saw a tool result


def _agent_client(driver: Driver, trail: Any) -> TestClient:
    """A minimal app with the agent router and the global error handlers."""
    from chorus.api.routers import agent as agent_router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(agent_router.router)
    app.state.driver = driver
    app.state.audit = trail
    return TestClient(app)


def test_agent_route_answers_428_without_a_purpose(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /agent/query is refused up front when its actions need a purpose."""
    calls = _script(monkeypatch, [_FakeMessage(content="unused")])
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"ai_call"}))
    resp = _agent_client(migrated_driver, trail).post(
        "/agent/query", json={"messages": _QUESTION}, headers={"X-Auth-User": "alice-example"}
    )
    trail.close()

    assert resp.status_code == 428
    assert calls == []
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("ai_call", "agent.answer", "denied")
    assert event["actor"] == {"user": "alice-example", "via": "edge"}
    assert_chain_ok(stream)


def test_agent_route_fails_closed_when_the_turn_cannot_be_recorded(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed audit write makes the agent request a generic 500 that carries no answer."""
    _script(monkeypatch, [_FakeMessage(content="answer-text-0001")])
    stream = io.StringIO()
    trail = started_trail(stream)
    stream.close()
    resp = _agent_client(migrated_driver, trail).post(
        "/agent/query", json={"messages": _QUESTION}, headers={"X-Auth-User": "alice-example"}
    )
    atexit.unregister(trail.emitter.close)  # the broken chain cannot write its shutdown event
    assert resp.status_code == 500
    assert "answer-text-0001" not in resp.text
