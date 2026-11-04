"""Every tool call over POST /tools/{name} writes exactly one query event.

Drives each registered tool through the real router against a small seeded
graph and checks the event: action, operation, outcome, actor, a ``mixed``
target with kind-prefixed ids and the result count, the parameters as
criteria, and no content. All names and texts are invented.
"""

from __future__ import annotations

import atexit
import io
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Driver

from chorus.api.errors import install_error_handlers
from chorus.audit.trail import AuditTrail
from chorus.utils.audit_runtime import encode_purpose
from tests._audit import assert_chain_ok, operation_events, started_trail

_TEXT = "placeholder post text"
_HEADERS = {"X-Auth-User": "alice-example"}

_INPUTS: dict[str, dict[str, Any]] = {
    "posts_mentioning": {"entity": "Example Town"},
    "authors_mentioning": {"entity": "Example Town"},
    "author_activity_summary": {"author": "quietfjord"},
    "topic_co_occurrence": {"topic": "Example Town"},
    "authors_connected_by_topic": {"seed_author": "quietfjord"},
    "network_around": {"entity": "Example Town", "depth": 2},
    "social_network_around": {"author": "quietfjord", "depth": 1},
    "expand_network_node": {"node_id": "author:auth-1"},
    "expand_social_node": {"author_id": "auth-1"},
}


def _seed(driver: Driver) -> None:
    """Two authors who post about two resolved topics and one unresolved one, and know each other."""
    with driver.session() as s:
        s.run(
            """
            MERGE (a1:Author {id: 'auth-1'}) ON CREATE SET a1.handle = 'quietfjord', a1.display_name = 'Example One'
            MERGE (a2:Author {id: 'auth-2'}) ON CREATE SET a2.handle = 'mossyriver', a2.display_name = 'Example Two'
            MERGE (town:Alias {surface_form: 'Example Town'})
            MERGE (harbor:Alias {surface_form: 'Harbor Works'})
            MERGE (works:Alias {surface_form: 'Example Works'})
            MERGE (e1:Entity {id: 'ent-1'}) ON CREATE SET e1.canonical_name = 'Example Town', e1.type = 'LOC'
            MERGE (e2:Entity {id: 'ent-2'}) ON CREATE SET e2.canonical_name = 'Example Works', e2.type = 'ORG'
            MERGE (town)-[:RESOLVED_TO]->(e1)
            MERGE (works)-[:RESOLVED_TO]->(e2)
            MERGE (p1:Post:Posting {uuid: 'p-1'})
              ON CREATE SET p1.text = $text, p1.timestamp = datetime('2026-05-01T10:00:00+00:00')
            MERGE (p2:Post:Posting {uuid: 'p-2'})
              ON CREATE SET p2.text = $text, p2.timestamp = datetime('2026-05-02T10:00:00+00:00')
            MERGE (a1)-[:AUTHORED]->(p1)
            MERGE (a2)-[:AUTHORED]->(p2)
            MERGE (p1)-[:MENTIONS]->(town)
            MERGE (p2)-[:MENTIONS]->(town)
            MERGE (p2)-[:MENTIONS]->(harbor)
            MERGE (p2)-[:MENTIONS]->(works)
            MERGE (a2)-[:FOLLOWS]->(a1)
            MERGE (a1)-[:FRIENDS_WITH]->(a2)
            """,
            text=_TEXT,
        )


def _client(driver: Any, trail: AuditTrail, *, raise_server_exceptions: bool = True) -> TestClient:
    """A minimal app with the tools router and the global error handlers."""
    from chorus.api.routers import tools as tools_router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(tools_router.router)
    app.state.driver = driver
    app.state.audit = trail
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_every_tool_is_covered() -> None:
    """A new tool must be added to this module's inputs."""
    from chorus.tools import TOOLS

    assert set(TOOLS) == set(_INPUTS)


@pytest.mark.parametrize("name", sorted(_INPUTS))
def test_tool_call_writes_one_query_event(
    migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO, name: str
) -> None:
    """One query event, the caller as actor, a mixed target sized like the result, no content."""
    from chorus.tools import TOOLS

    _seed(migrated_driver)
    payload = _INPUTS[name]
    resp = _client(migrated_driver, audit_trail).post(
        f"/tools/{name}", json=payload, headers={**_HEADERS, "X-Request-ID": "req-0001"}
    )
    assert resp.status_code == 200, resp.text

    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", f"tool.{name}", "ok")
    assert event["actor"] == {"user": "alice-example", "via": "edge"}
    assert event["request_id"] == "req-0001"
    assert payload.items() <= event["criteria"].items()
    target = event["target"]
    assert target["kind"] == "mixed"
    result = TOOLS[name].output_model.model_validate(resp.json())
    assert target["count"] == getattr(result, "audit_result_count")()  # noqa: B009
    ids = target.get("ids", [])
    assert ids, "the seeded graph gives every tool something to reference"
    assert all(ref.split(":", 1)[0] in {"author", "entity", "post"} for ref in ids)
    kinds = [ref.split(":", 1)[0] for ref in ids]
    assert kinds == sorted(kinds, key=["author", "entity", "post"].index)  # data subjects first
    assert _TEXT not in audit_stream.getvalue()


def test_stated_purpose_reaches_the_tool_event(
    migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """The decoded X-Audit-Purpose lands on the tool's event."""
    _seed(migrated_driver)
    headers = {**_HEADERS, "X-Audit-Purpose": encode_purpose("case-0001", "review")}
    resp = _client(migrated_driver, audit_trail).post(
        "/tools/posts_mentioning", json={"entity": "Example Town"}, headers=headers
    )
    assert resp.status_code == 200
    [event] = operation_events(audit_stream)
    assert event["purpose"] == {"case_ref": "case-0001", "reason": "review"}


def test_unknown_tool_and_invalid_payload_write_nothing(
    migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """No operation ran, so a 404 or a 422 leaves no event."""
    client = _client(migrated_driver, audit_trail)
    assert client.post("/tools/does_not_exist", json={}, headers=_HEADERS).status_code == 404
    assert client.post("/tools/posts_mentioning", json={"limit": 0}, headers=_HEADERS).status_code == 422
    assert operation_events(audit_stream) == []


def test_required_purpose_refuses_a_tool_call(migrated_driver: Driver) -> None:
    """With query required, a tool call without a purpose is a 428 and one denied event."""
    _seed(migrated_driver)
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query"}))
    resp = _client(migrated_driver, trail).post(
        "/tools/posts_mentioning", json={"entity": "Example Town"}, headers=_HEADERS
    )
    trail.close()

    assert resp.status_code == 428
    assert _TEXT not in resp.text
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "tool.posts_mentioning", "denied")
    assert event["criteria"]["entity"] == "Example Town"
    assert "target" not in event
    assert_chain_ok(stream)


def test_failed_audit_write_returns_no_data(migrated_driver: Driver) -> None:
    """When the query event cannot be written, the call is a generic 500 without the hits."""
    _seed(migrated_driver)
    stream = io.StringIO()
    trail = started_trail(stream)
    stream.close()
    resp = _client(migrated_driver, trail).post(
        "/tools/posts_mentioning", json={"entity": "Example Town"}, headers=_HEADERS
    )
    atexit.unregister(trail.emitter.close)  # the broken chain cannot write its shutdown event
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error."}
    assert _TEXT not in resp.text
    assert "p-1" not in resp.text


class _FailingDriver:
    """A driver whose sessions cannot be opened, so the tool raises."""

    def session(self) -> Any:
        """Fail like an unreachable database."""
        raise RuntimeError("database unreachable")


def test_failed_tool_writes_an_error_event(audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """A tool that raises leaves one error event with its criteria and no target."""
    resp = _client(_FailingDriver(), audit_trail, raise_server_exceptions=False).post(
        "/tools/posts_mentioning", json={"entity": "Example Town"}, headers=_HEADERS
    )
    assert resp.status_code == 500
    [event] = operation_events(audit_stream)
    assert (event["operation"], event["outcome"]) == ("tool.posts_mentioning", "error")
    assert event["criteria"]["entity"] == "Example Town"
    assert "target" not in event
    assert "unreachable" not in audit_stream.getvalue()
