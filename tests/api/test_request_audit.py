"""The request audit seam: actor, X-Audit-Purpose, X-Request-ID, 400/428, fail-closed.

A probe route on a minimal app depends on ``request_audit`` exactly like the
audited routers do, so these cases need no Neo4j. All values are invented.
"""

from __future__ import annotations

import atexit
import io

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chorus.api.audit import request_audit
from chorus.api.errors import install_error_handlers
from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_runtime import encode_purpose
from tests._audit import assert_chain_ok, operation_events, started_trail

SECRET = "edge-secret-for-tests-0123456789abcdef"
_DATA = "probe-payload-0001"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Neutral principal env: no edge secret, no default identity.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        The same monkeypatch, for the test's own overrides.
    """
    for key in ("CHORUS_EDGE_SECRET", "CHORUS_DEFAULT_IDENTITY", "CHORUS_AUTH_HEADER", "CHORUS_DISPLAY_NAME_HEADER"):
        monkeypatch.setenv(key, "")
    return monkeypatch


def _client(trail: AuditTrail) -> TestClient:
    """A minimal app with one audited probe route and the global error handlers."""
    app = FastAPI()
    install_error_handlers(app)
    app.state.audit = trail

    @app.post("/probe")
    def probe(audit: AuditContext = Depends(request_audit)) -> dict[str, str]:  # noqa: B008
        audit.require_purpose(("query",), "probe.read", {"k": 1})
        audit.emit_refs("query", "probe.read", kind="mixed", ids=["author:a-1"], count=1, criteria={"k": 1})
        return {"data": _DATA}

    return TestClient(app)


def test_stated_purpose_is_recorded(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """X-Audit-Purpose is decoded into the event's purpose (UTF-8, form-encoded)."""
    header = encode_purpose("case-0001", "Prüfung des Hinweises")
    resp = _client(audit_trail).post("/probe", headers={"X-Auth-User": "alice-example", "X-Audit-Purpose": header})
    assert resp.status_code == 200
    [event] = operation_events(audit_stream)
    assert event["purpose"] == {"case_ref": "case-0001", "reason": "Prüfung des Hinweises"}
    assert event["actor"] == {"user": "alice-example", "via": "edge"}


@pytest.mark.parametrize(
    "header",
    [
        b"case_ref=a&case_ref=b",
        b"reason=%FF",
        b"case_ref=" + b"x" * 300,
        b"reason=line%0Abreak",
        "Prüfung".encode(),
    ],
    ids=["repeated", "not-utf8", "oversized", "control-char", "not-ascii"],
)
def test_malformed_purpose_is_a_400_and_emits_nothing(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO, header: bytes
) -> None:
    """A malformed purpose is no valid operation yet: 400, generic body, no event."""
    headers = {b"X-Auth-User": b"alice-example", b"X-Audit-Purpose": header}
    resp = _client(audit_trail).post("/probe", headers=headers)
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Invalid request."}
    assert operation_events(audit_stream) == []


def test_missing_purpose_is_a_428_with_one_denied_event(env: pytest.MonkeyPatch) -> None:
    """With query required, a request without a purpose is refused and the refusal recorded."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query"}))
    resp = _client(trail).post("/probe", headers={"X-Auth-User": "alice-example", "X-Request-ID": "req-0001"})
    trail.close()

    assert resp.status_code == 428
    assert resp.json() == {"detail": "A stated purpose is required for this request."}
    assert _DATA not in resp.text
    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("query", "probe.read", "denied")
    assert event["actor"] == {"user": "alice-example", "via": "edge"}
    assert event["criteria"] == {"k": 1}
    assert event["request_id"] == "req-0001"
    assert "target" not in event
    assert_chain_ok(stream)


def test_required_purpose_passes_when_stated(env: pytest.MonkeyPatch) -> None:
    """With query required, a stated purpose lets the request through."""
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({"query"}))
    resp = _client(trail).post(
        "/probe", headers={"X-Auth-User": "alice-example", "X-Audit-Purpose": encode_purpose("case-0001", None)}
    )
    trail.close()
    assert resp.status_code == 200
    [event] = operation_events(stream)
    assert event["outcome"] == "ok"
    assert event["purpose"] == {"case_ref": "case-0001"}
    assert_chain_ok(stream)


def test_no_428_when_nothing_is_required(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """The default empty AUDIT_REQUIRE_PURPOSE refuses nothing."""
    resp = _client(audit_trail).post("/probe", headers={"X-Auth-User": "alice-example"})
    assert resp.status_code == 200
    [event] = operation_events(audit_stream)
    assert "purpose" not in event


def test_dev_default_identity_is_a_system_actor(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """Without an identity header the dev fallback identity acts, via system."""
    env.setenv("CHORUS_DEFAULT_IDENTITY", "dev-default")
    resp = _client(audit_trail).post("/probe")
    assert resp.status_code == 200
    [event] = operation_events(audit_stream)
    assert event["actor"] == {"user": "dev-default", "via": "system"}


def test_verified_edge_identity_is_an_edge_actor(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """With the edge secret set and matched, the gateway-asserted user acts, via edge."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    resp = _client(audit_trail).post("/probe", headers={"X-Auth-User": "alice-example", "X-Edge-Secret": SECRET})
    assert resp.status_code == 200
    [event] = operation_events(audit_stream)
    assert event["actor"] == {"user": "alice-example", "via": "edge"}


@pytest.mark.parametrize("default_identity", ["", "dev-default"])
def test_unverified_identity_is_a_401_and_not_audited(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO, default_identity: str
) -> None:
    """A forged identity (wrong edge secret) is unauthenticated: 401, no event, fallback or not."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    env.setenv("CHORUS_DEFAULT_IDENTITY", default_identity)
    resp = _client(audit_trail).post(
        "/probe", headers={"X-Auth-User": "alice-example", "X-Edge-Secret": SECRET[:-1] + "0"}
    )
    assert resp.status_code == 401
    assert operation_events(audit_stream) == []


def test_unauthenticated_request_is_not_audited(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """No identity and no fallback: 401 and nothing written."""
    resp = _client(audit_trail).post("/probe", headers={"X-Audit-Purpose": "case_ref=case-0001"})
    assert resp.status_code == 401
    assert operation_events(audit_stream) == []


@pytest.mark.parametrize(
    ("sent", "recorded"),
    [("8d7c2a4e-0f1b-4c3d-9e8f-123456789abc", "8d7c2a4e-0f1b-4c3d-9e8f-123456789abc"), ("not a sane id", None)],
    ids=["uuid", "junk"],
)
def test_request_id_is_kept_only_when_sane(
    env: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO, sent: str, recorded: str | None
) -> None:
    """X-Request-ID is copied onto the event only when it looks like a correlation id."""
    _client(audit_trail).post("/probe", headers={"X-Auth-User": "alice-example", "X-Request-ID": sent})
    [event] = operation_events(audit_stream)
    assert event.get("request_id") == recorded


def test_audit_write_failure_is_a_500_without_data(env: pytest.MonkeyPatch) -> None:
    """When the event cannot be written, the request fails generically and returns no data."""
    stream = io.StringIO()
    trail = started_trail(stream)
    stream.close()
    resp = _client(trail).post("/probe", headers={"X-Auth-User": "alice-example"})
    atexit.unregister(trail.emitter.close)  # the broken chain cannot write its shutdown event
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error."}
    assert _DATA not in resp.text
