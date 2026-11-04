"""The compose default purpose policy against every audited route the SPA calls.

``docker/compose.yaml`` gives the backend ``AUDIT_REQUIRE_PURPOSE=query,disclose``
(§76 BDSG Abs. 2 asks the reason for queries and disclosures). Under that
policy the SPA's query routes — tool calls, ``/stats``, agent turns — are
refused without a stated purpose and served with one, while the ingestion
routes (collection, combination, modification, administration) still run
without one. The policy is read from the compose file, so these tests follow
a change to it. All names and texts are invented.
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Driver

from chorus.api.errors import install_error_handlers
from chorus.audit.trail import PURPOSE_ACTIONS, AuditTrail
from chorus.ingestion.jobs import JobRegistry
from chorus.utils.audit_runtime import encode_purpose, parse_required_actions
from tests._audit import assert_chain_ok, operation_events, started_trail

_COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "compose.yaml"
_HEADERS = {"X-Auth-User": "alice-example"}
_PURPOSE = {"X-Audit-Purpose": encode_purpose("case-0001", None)}
_POSTINGS_CSV = (
    b"UUID,Posting ID,Text Content,Timestamp,Crawled at,Author ID,Author,Network,Tags\r\n"
    b"p-1,post-net-1,placeholder post text,2026-05-01T10:00:00+00:00,2026-05-02T10:00:00+00:00,"
    b"a-1,Alice Example,examplenet,news\r\n"
)

_QUERY_ROUTES = [
    pytest.param(
        "post", "/tools/posts_mentioning", {"json": {"entity": "Example Town"}}, "tool.posts_mentioning", id="tool"
    ),
    pytest.param("get", "/stats", {}, "stats.read", id="stats"),
    pytest.param(
        "post",
        "/agent/query",
        {"json": {"messages": [{"role": "user", "content": "Who posts about Example Town?"}]}},
        "agent.answer",
        id="agent",
    ),
]


def _compose_default() -> frozenset[str]:
    """Return the ``AUDIT_REQUIRE_PURPOSE`` default that ``docker/compose.yaml`` gives the backend."""
    match = re.search(r"AUDIT_REQUIRE_PURPOSE: \$\{AUDIT_REQUIRE_PURPOSE-([a-z_,]*)\}", _COMPOSE.read_text())
    assert match is not None, "docker/compose.yaml sets no colon-less AUDIT_REQUIRE_PURPOSE default"
    return parse_required_actions(match.group(1), known=PURPOSE_ACTIONS)


def _build_app(driver: Driver, trail: AuditTrail, jobs: JobRegistry) -> FastAPI:
    """Assemble the audited routers the SPA calls, with the global error handlers."""
    from chorus.api.routers import agent as agent_router
    from chorus.api.routers import ingestion as ingestion_router
    from chorus.api.routers import stats as stats_router
    from chorus.api.routers import tools as tools_router

    app = FastAPI()
    install_error_handlers(app)
    for router in (
        tools_router.router,
        stats_router.router,
        agent_router.router,
        ingestion_router.status_router,
        ingestion_router.router,
    ):
        app.include_router(router)
    app.state.driver = driver
    app.state.audit = trail
    app.state.jobs = jobs
    return app


def _await_job(client: TestClient, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    """Poll GET /ingestion/jobs/{id} until the job is terminal or time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/ingestion/jobs/{job_id}", headers=_HEADERS).json()
        if body["status"] in ("done", "error"):
            return cast(dict[str, Any], body)
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _unit_vector() -> list[float]:
    """A finite, positive-norm 1024-d embedding (zero vectors fail the index)."""
    return [1.0] + [0.0] * 1023


def test_compose_default_requires_query_and_disclose() -> None:
    """A deployment needs a stated purpose for queries and disclosures, and for nothing else."""
    assert _compose_default() == frozenset({"query", "disclose"})


@pytest.mark.parametrize(("method", "path", "kwargs", "operation"), _QUERY_ROUTES)
def test_query_routes_refuse_without_a_purpose(
    migrated_driver: Driver, method: str, path: str, kwargs: dict[str, Any], operation: str
) -> None:
    """Under the compose default a query without a stated purpose is a 428 and one denied event."""
    stream = io.StringIO()
    trail = started_trail(stream, required=_compose_default())
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(migrated_driver, trail, jobs)).request(method, path, headers=_HEADERS, **kwargs)
    finally:
        jobs.shutdown()
        trail.close()

    assert resp.status_code == 428, resp.text
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", operation, "denied")
    assert "target" not in event
    assert_chain_ok(stream)


@pytest.mark.parametrize(("method", "path", "kwargs", "operation"), _QUERY_ROUTES[:2])
def test_query_routes_answer_with_a_purpose(
    migrated_driver: Driver, method: str, path: str, kwargs: dict[str, Any], operation: str
) -> None:
    """With a stated purpose the same queries answer, and their event records it."""
    stream = io.StringIO()
    trail = started_trail(stream, required=_compose_default())
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(migrated_driver, trail, jobs)).request(
            method, path, headers={**_HEADERS, **_PURPOSE}, **kwargs
        )
    finally:
        jobs.shutdown()
        trail.close()

    assert resp.status_code == 200, resp.text
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", operation, "ok")
    assert event["purpose"] == {"case_ref": "case-0001"}
    assert_chain_ok(stream)


@pytest.mark.parametrize(
    ("path", "data", "operations"),
    [
        pytest.param("/ingestion/ingest", None, ["ingest.upload", "ingest.ner", "ingest.staging.delete"], id="upload"),
        pytest.param(
            "/ingestion/ingest",
            {"then_resolve": "true"},
            ["ingest.upload", "ingest.ner", "resolution.run", "resolution.embed", "ingest.staging.delete"],
            id="upload-then-resolve",
        ),
        pytest.param("/ingestion/resolve", None, ["resolution.run", "resolution.embed"], id="resolve"),
    ],
)
def test_ingestion_jobs_run_without_a_purpose(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    data: dict[str, str] | None,
    operations: list[str],
) -> None:
    """Uploads and resolution (collect, combine, modify, ai_call) need no purpose under the compose default."""
    from chorus.inference import ner_client, provider

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setenv("NER_ENABLED", "true")
    monkeypatch.setattr(ner_client, "extract_entities", lambda text, **kw: [])
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_unit_vector() for _ in texts])
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Example Works'})")

    stream = io.StringIO()
    trail = started_trail(stream, required=_compose_default())
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, trail, jobs))
        kwargs: dict[str, Any] = {"headers": _HEADERS}
        if path == "/ingestion/ingest":
            kwargs["files"] = [("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))]
            kwargs["data"] = data
        resp = client.post(path, **kwargs)
        assert resp.status_code == 202, resp.text
        assert _await_job(client, resp.json()["job_id"])["status"] == "done"
    finally:
        jobs.shutdown()
        trail.close()

    events = operation_events(stream)
    assert [e["operation"] for e in events] == operations
    assert all(e["outcome"] == "ok" and "purpose" not in e for e in events)
    assert_chain_ok(stream)


def test_migrate_runs_without_a_purpose(driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """Applying migrations (admin, modify) needs no purpose under the compose default."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    stream = io.StringIO()
    trail = started_trail(stream, required=_compose_default())
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(driver, trail, jobs)).post("/ingestion/migrate", headers=_HEADERS)
    finally:
        jobs.shutdown()
        trail.close()

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"]
    events = operation_events(stream)
    assert events and all(e["operation"] == "migration.apply" and e["outcome"] == "ok" for e in events)
    assert_chain_ok(stream)
