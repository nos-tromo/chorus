"""Global error handlers: generic client-visible bodies, full detail to logs.

Covers `install_error_handlers` directly (Template C), plus the specific
sweep sites that used to leak exception text into response bodies: the
agent route, tool-input validation, and ingestion job failures.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import BaseModel

from chorus.api.errors import install_error_handlers

MARKER = "MARKER-SECRET-1234"


class _ValidatedPayload(BaseModel):
    value: int


def _capture_logs() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    return records, sink_id


def _build_boom_app() -> FastAPI:
    """Minimal app with the global handlers and a route that always raises."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError(MARKER)

    return app


def test_unhandled_error_is_generic_and_logged() -> None:
    """A raising endpoint returns a generic body; the marker only appears in logs."""
    records, sink_id = _capture_logs()
    try:
        client = TestClient(_build_boom_app(), raise_server_exceptions=False)
        resp = client.get("/boom")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error."}
        assert MARKER not in resp.text
        assert any(MARKER in r for r in records)
    finally:
        logger.remove(sink_id)


def _build_validation_app() -> FastAPI:
    """Minimal app with the global handlers and a body-validated route."""
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/validated")
    def validated(payload: _ValidatedPayload) -> dict[str, int]:
        return {"value": payload.value}

    return app


def test_request_validation_error_is_generic_and_logged() -> None:
    """A malformed request body returns a generic body; details go to logs."""
    records, sink_id = _capture_logs()
    try:
        client = TestClient(_build_validation_app())
        resp = client.post("/validated", json={"value": MARKER})
        assert resp.status_code == 422
        assert resp.json() == {"detail": "Invalid request."}
        assert MARKER not in resp.text
        assert any(MARKER in r for r in records)
    finally:
        logger.remove(sink_id)


def _build_agent_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Minimal app with the agent router, stubbed state, and global handlers."""
    from chorus.api.routers import agent as agent_router

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(MARKER)

    monkeypatch.setattr(agent_router, "run_agent", _boom)

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(agent_router.router)
    app.state.driver = object()
    app.state.audit = object()
    return app


def test_agent_query_failure_is_generic_and_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent-loop crash never puts the raw exception text in the response body."""
    monkeypatch.setenv("CHORUS_DEFAULT_IDENTITY", "test-user")
    records, sink_id = _capture_logs()
    try:
        client = TestClient(_build_agent_app(monkeypatch), raise_server_exceptions=False)
        resp = client.post("/agent/query", json={"messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error."}
        assert MARKER not in resp.text
        assert any(MARKER in r for r in records)
    finally:
        logger.remove(sink_id)


def _build_tools_app() -> FastAPI:
    """Minimal app with the real tools router and global handlers."""
    from chorus.api.routers import tools as tools_router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(tools_router.router)
    app.state.driver = object()
    app.state.audit = object()
    return app


def test_tool_input_validation_error_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid tool payload returns a generic body, no pydantic error structure."""
    monkeypatch.setenv("CHORUS_DEFAULT_IDENTITY", "test-user")
    client = TestClient(_build_tools_app())
    # posts_mentioning requires "entity"; omit it to trigger a ValidationError.
    resp = client.post("/tools/posts_mentioning", json={})
    assert resp.status_code == 422
    assert resp.json() == {"detail": "Invalid request."}
    assert "loc" not in resp.text
    assert "msg" not in resp.text


def test_job_failure_records_generic_error_and_logs_detail() -> None:
    """A raising job ends with a static error field; the marker only appears in logs."""
    import time

    from chorus.ingestion.jobs import Job, JobRegistry

    def _boom(_job: Job) -> dict[str, Any]:
        raise RuntimeError(MARKER)

    records, sink_id = _capture_logs()
    reg = JobRegistry()
    try:
        job = reg.submit("ingest", _boom)
        # The job's terminal state is recorded slightly before its log line is
        # emitted (see JobRegistry._run), so poll for both rather than just
        # the status — otherwise this races the logging call.
        deadline = time.monotonic() + 5.0
        terminal = None
        while time.monotonic() < deadline:
            candidate = reg.get(job.id)
            if candidate is not None and candidate.status in ("done", "error") and records:
                terminal = candidate
                break
            time.sleep(0.01)
        assert terminal is not None, "job did not reach a terminal, logged state in time"
        assert terminal.status == "error"
        assert terminal.error == "Job failed."
        assert MARKER not in (terminal.error or "")
        assert any(MARKER in r for r in records)
    finally:
        logger.remove(sink_id)
        reg.shutdown()
