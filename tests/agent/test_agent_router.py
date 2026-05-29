"""POST /agent/query wiring, with a stubbed provider and a minimal app."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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


def test_agent_query_endpoint_returns_answer_and_trace(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /agent/query runs the loop and returns answer + trace as JSON."""
    from chorus.api.routers import agent as agent_router
    from chorus.inference import provider

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

    responses = iter(
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "posts_mentioning", '{"entity": "Berlin"}')]),
            _FakeMessage(content="One post mentions Berlin."),
        ]
    )
    monkeypatch.setattr(provider, "chat_message", lambda messages, **kwargs: next(responses))

    app = FastAPI()
    app.include_router(agent_router.router)
    app.state.driver = migrated_driver
    app.state.audit = in_memory_audit

    client = TestClient(app)
    resp = client.post(
        "/agent/query",
        json={"messages": [{"role": "user", "content": "posts about Berlin?"}]},
        headers={"X-Auth-User": "analyst"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "One post mentions Berlin."
    assert body["trace"][0]["tool"] == "posts_mentioning"
    assert body["truncated"] is False
