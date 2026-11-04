"""GET /config endpoint: language + ingestion flag + version + purpose policy."""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chorus.audit.trail import AuditTrail
from chorus.utils.audit_event import AuditEmitter
from chorus.utils.audit_runtime import encode_purpose
from tests._audit import assert_chain_ok, operation_events, started_trail


def _build_app(trail: AuditTrail | None = None) -> FastAPI:
    """Minimal app with only the config router (no lifespan, no Neo4j).

    Args:
        trail: The process's audit trail; an unstarted one requiring
            nothing when omitted (the route only reads its policy).
    """
    from chorus.api.routers import config as config_router

    app = FastAPI()
    app.include_router(config_router.router)
    app.state.audit = trail or AuditTrail(AuditEmitter("chorus", "test", stream=io.StringIO(), heartbeat_interval_s=0))
    return app


def test_config_reports_language_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /config returns language=de and ingestion_enabled=true when both env vars are set."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    client = TestClient(_build_app())
    resp = client.get("/config")  # no auth header
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "de"
    assert body["ingestion_enabled"] is True
    assert isinstance(body["version"], str) and body["version"]


def test_config_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /config returns safe defaults (en, ingestion off) when env vars are absent."""
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "en"
    assert body["ingestion_enabled"] is False
    assert body["require_purpose"] == []


def test_config_reports_the_actions_that_need_a_purpose() -> None:
    """GET /config lists, sorted, the actions the trail refuses without a stated purpose."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query", "disclose"}))
    resp = TestClient(_build_app(trail)).get("/config")
    trail.close()

    assert resp.status_code == 200
    assert resp.json()["require_purpose"] == ["disclose", "query"]
    assert_chain_ok(stream)


def test_config_is_not_audited() -> None:
    """GET /config writes no event, even with a purpose stated and one required."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query"}))
    resp = TestClient(_build_app(trail)).get(
        "/config",
        headers={"X-Auth-User": "alice-example", "X-Audit-Purpose": encode_purpose("case-0001", None)},
    )
    trail.close()

    assert resp.status_code == 200
    assert operation_events(stream) == []
    assert_chain_ok(stream)
