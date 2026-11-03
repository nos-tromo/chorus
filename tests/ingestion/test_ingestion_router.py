"""/ingestion/* router: feature gate, feature status, migrations, migrate.

Gate and feature-status cases need no Neo4j and run fast; migrations/migrate
cases use the driver fixtures (and so the session Neo4j testcontainer). The
worker-backed ingest/resolve routes are covered in later commits.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from neo4j import Driver

from chorus.audit.trail import AuditTrail
from chorus.ingestion.jobs import Job, JobRegistry
from chorus.utils.audit_runtime import encode_purpose
from tests._audit import assert_chain_ok, operation_events, started_trail

_MARKER = "MARKER-SECRET-1234"

_POSTINGS_CSV = (
    b"UUID,Posting ID,Text Content,Timestamp,Crawled at,Author ID,Author,Network,Tags\r\n"
    b"p-1,post-net-1,hello berlin,2026-05-01T10:00:00+00:00,2026-05-02T10:00:00+00:00,"
    b'a-1,Alice,linkedin,"news, politics"\r\n'
)


def _unit_vector() -> list[float]:
    """A finite, positive-norm 1024-d embedding (zero vectors fail the index)."""
    return [1.0] + [0.0] * 1023


def _await_job(client: TestClient, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    """Poll GET /ingestion/jobs/{id} until the job is terminal or time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/ingestion/jobs/{job_id}", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("done", "error"):
            return cast(dict[str, Any], body)
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _build_app(driver: Any, audit: Any, jobs: JobRegistry) -> FastAPI:
    """Assemble a minimal app with both ingestion routers and shared state."""
    from chorus.api.routers import ingestion as ingestion_router

    app = FastAPI()
    app.include_router(ingestion_router.status_router)
    app.include_router(ingestion_router.router)
    app.state.driver = driver
    app.state.audit = audit
    app.state.jobs = jobs
    return app


def _ops(stream: io.StringIO) -> list[str]:
    """The operations recorded so far, in order."""
    return [e["operation"] for e in operation_events(stream)]


# --------------------------------------------------------------------------
# Feature gate + status (no Neo4j)
# --------------------------------------------------------------------------


def test_feature_reports_disabled_by_default(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """GET /ingestion/feature answers even when the flag is off."""
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, audit_trail, jobs)).get(
            "/ingestion/feature", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False}
    finally:
        jobs.shutdown()


def test_feature_reports_enabled_when_flag_set(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """GET /ingestion/feature reflects INGESTION_UI_ENABLED=true."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, audit_trail, jobs)).get(
            "/ingestion/feature", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True}
    finally:
        jobs.shutdown()


@pytest.mark.parametrize(
    ("method", "path", "refused"),
    [
        ("get", "/ingestion/migrations", None),
        ("post", "/ingestion/migrate", ("admin", "migration.apply")),
        ("post", "/ingestion/resolve", ("combine", "resolution.run")),
        ("get", "/ingestion/jobs/job-1", None),
    ],
)
def test_gated_routes_403_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    method: str,
    path: str,
    refused: tuple[str, str] | None,
) -> None:
    """Action/data routes are forbidden when the feature flag is off; refused actions are recorded."""
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, audit_trail, jobs)).request(method, path, headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 403
    finally:
        jobs.shutdown()
    events = operation_events(audit_stream)
    if refused is None:
        assert events == []  # status routes are not audited operations
    else:
        [event] = events
        assert (event["action"], event["operation"], event["outcome"]) == (*refused, "denied")
        assert event["actor"] == {"user": "analyst", "via": "edge"}


def test_gated_route_401_without_principal(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """A gated route requires authentication even when enabled."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, audit_trail, jobs)).get("/ingestion/migrations")
        assert resp.status_code == 401
    finally:
        jobs.shutdown()


# --------------------------------------------------------------------------
# Migrations status + apply (Neo4j)
# --------------------------------------------------------------------------


def test_migrations_status_on_fresh_db(
    driver: Driver, monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail
) -> None:
    """On a fresh DB, everything is pending and nothing is applied."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(driver, audit_trail, jobs)).get(
            "/ingestion/migrations", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] == []
        assert body["pending"], "expected pending migrations on a fresh DB"
    finally:
        jobs.shutdown()


def test_migrations_status_after_migrate(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail
) -> None:
    """On a migrated DB, nothing is pending and versions are applied."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(migrated_driver, audit_trail, jobs)).get(
            "/ingestion/migrations", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"], "expected applied migrations after migrate"
        assert body["pending"] == []
    finally:
        jobs.shutdown()


def test_migrate_applies_on_fresh_db_and_audits(
    driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /ingestion/migrate applies pending migrations and records each one for the caller."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(driver, audit_trail, jobs)).post(
            "/ingestion/migrate", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        applied = resp.json()["applied"]
        assert applied, "expected freshly applied versions"

        events = operation_events(audit_stream)
        assert [e["target"]["ids"] for e in events] == [[v] for v in applied]
        assert all((e["action"], e["operation"]) == ("admin", "migration.apply") for e in events)
        assert all(e["actor"] == {"user": "analyst", "via": "edge"} for e in events)
    finally:
        jobs.shutdown()


def test_migrate_noop_on_migrated_db_records_nothing(
    migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migrate with nothing pending returns [] and records nothing: nothing happened."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(migrated_driver, audit_trail, jobs)).post(
            "/ingestion/migrate", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] == []
        assert operation_events(audit_stream) == []
    finally:
        jobs.shutdown()


def test_migrate_409_when_a_job_is_active(
    migrated_driver: Driver, audit_trail: AuditTrail, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrate is rejected while a background job is in flight (no DDL interleave)."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    started, release = threading.Event(), threading.Event()

    def _block(_job: Job) -> dict[str, Any]:
        started.set()
        release.wait(timeout=5.0)
        return {"ok": True}

    try:
        jobs.submit("ingest", _block)
        assert started.wait(timeout=5.0)
        resp = TestClient(_build_app(migrated_driver, audit_trail, jobs)).post(
            "/ingestion/migrate", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 409
    finally:
        release.set()
        jobs.shutdown()


# --------------------------------------------------------------------------
# Upload + ingest job + job status
# --------------------------------------------------------------------------


def test_ingest_happy_path_runs_job_and_audits(
    migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid postings.csv runs to done: one collect event, then the staged copy's deletion."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setenv("NER_ENABLED", "false")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 202, resp.text
        accepted = resp.json()
        assert accepted["kind"] == "ingest"

        done = _await_job(client, accepted["job_id"])
        assert done["status"] == "done", done
        assert done["result"]["counts"]["postings"] == 1

        with migrated_driver.session() as s:
            rec = s.run("MATCH (p:Post:Posting {uuid:'p-1'}) RETURN count(p) AS n").single()
        assert rec is not None and rec["n"] == 1

        collect, deleted = operation_events(audit_stream)
        assert (collect["action"], collect["operation"], collect["outcome"]) == ("collect", "ingest.upload", "ok")
        assert collect["actor"] == {"user": "analyst", "via": "edge"}
        assert collect["target"] == {"kind": "author", "ids": ["a-1"], "count": 1}
        assert collect["criteria"]["files"] == ["postings.csv"]
        assert collect["criteria"]["graph"]["postings"] == 1
        assert collect["criteria"]["raw_store"]["postings"] == 1
        assert (deleted["action"], deleted["operation"], deleted["outcome"]) == (
            "delete",
            "ingest.staging.delete",
            "ok",
        )
        assert deleted["target"] == {"kind": "file", "ids": ["postings.csv"], "count": 1, "container": "uploads"}
        assert "hello berlin" not in audit_stream.getvalue()
    finally:
        jobs.shutdown()


def test_ingest_records_the_ner_transfer(
    migrated_driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With NER on, the post texts sent to the NER service are one ai_call event per pass."""
    from chorus.inference import ner_client

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setenv("NER_ENABLED", "true")
    monkeypatch.setenv("NER_MODEL_VERSION", "example-ner-model")
    from chorus.inference.correlation import REQUEST_ID

    ids_at_ner: list[str | None] = []

    def _extract(text: str, **kw: Any) -> list[Any]:
        ids_at_ner.append(REQUEST_ID.get())  # what the NER call would forward
        return []

    monkeypatch.setattr(ner_client, "extract_entities", _extract)
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst", "X-Request-ID": "req-0001"},
        )
        assert _await_job(client, resp.json()["job_id"])["status"] == "done"
    finally:
        jobs.shutdown()
    assert ids_at_ner == ["req-0001"]  # the job carries its request's id to inference

    collect, ner, _deleted = operation_events(audit_stream)
    assert collect["request_id"] == "req-0001"
    assert (ner["action"], ner["operation"], ner["outcome"]) == ("ai_call", "ingest.ner", "ok")
    assert ner["recipient"] == "inference:ner"
    assert ner["target"] == {"kind": "post", "count": 1}
    assert ner["ai"] == {"model_alias": "example-ner-model", "sources": [collect["event_id"]]}
    assert ner["parent_event_id"] == collect["event_id"]


def test_ingest_rejects_unrecognized_filename(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """A mis-named upload is 422'd and no job is created (no silent 0-row run)."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("data.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 422
        assert resp.json() == {"detail": "Invalid request."}
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()


@pytest.mark.parametrize("evil", ["../postings.csv", "sub/postings.csv", "..\\postings.csv"])
def test_ingest_rejects_path_traversal_filename(
    monkeypatch: pytest.MonkeyPatch, evil: str, audit_trail: AuditTrail
) -> None:
    """A filename carrying a path or '..' is 422'd and never staged or run."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", (evil, _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 422, resp.text
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()


def test_ingest_rejects_bad_since(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """An unparseable 'since' is 422'd before any job is created."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            data={"since": "not-a-timestamp"},
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 422
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()


def test_ingest_requires_files(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """Posting with no files is rejected."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post("/ingestion/ingest", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 422
    finally:
        jobs.shutdown()


def test_ingest_403_when_disabled(
    monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail, audit_stream: io.StringIO
) -> None:
    """A valid upload is still forbidden when the feature flag is off, and the refusal recorded."""
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 403
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()
    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("collect", "ingest.upload", "denied")


def test_ingest_409_when_a_job_is_active(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """A second ingest is rejected while a job is in flight."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    started, release = threading.Event(), threading.Event()

    def _block(_job: Job) -> dict[str, Any]:
        started.set()
        release.wait(timeout=5.0)
        return {"ok": True}

    try:
        jobs.submit("ingest", _block)
        assert started.wait(timeout=5.0)
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 409
    finally:
        release.set()
        jobs.shutdown()


class _FailingAdapter:
    """An upstream adapter whose first read fails, like a vanished upload."""

    def fetch_postings(self, since: Any) -> Any:
        """Fail like an unreadable file."""
        raise RuntimeError("disk full")


def test_ingest_error_marks_job_error_and_cleans_staging(
    migrated_driver: Driver,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    chorus_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the pass raises, the job ends 'error', records an error collection, and removes staging."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    from chorus.api.routers import ingestion as ing

    monkeypatch.setattr(ing, "FileUpstreamAdapter", lambda _staging: _FailingAdapter())

    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 202
        done = _await_job(client, resp.json()["job_id"])
        assert done["status"] == "error"
        assert done["error"] == "Job failed."

        collect, deleted = operation_events(audit_stream)
        assert (collect["operation"], collect["outcome"]) == ("ingest.upload", "error")
        assert collect["target"] == {"kind": "author", "count": 0}
        assert "disk full" not in audit_stream.getvalue()
        assert (deleted["operation"], deleted["outcome"]) == ("ingest.staging.delete", "ok")

        uploads = chorus_env / "uploads"
        leftovers: list[Path] = list(uploads.iterdir()) if uploads.exists() else []
        assert leftovers == [], f"staging not cleaned: {leftovers}"
    finally:
        jobs.shutdown()


def test_ingest_then_resolve_chains_and_audits_twice(
    migrated_driver: Driver,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """then_resolve=true runs ingest then resolution in one job; both are recorded, then the cleanup."""
    from chorus.inference import provider

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setenv("NER_ENABLED", "false")
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_unit_vector() for _ in texts])
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Bratwurst'})")

    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            data={"then_resolve": "true"},
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 202, resp.text
        done = _await_job(client, resp.json()["job_id"])
        assert done["status"] == "done", done
        assert done["result"]["counts"]["postings"] == 1
        assert done["result"]["resolution"]["processed"] >= 1

        assert _ops(audit_stream) == ["ingest.upload", "resolution.run", "resolution.embed", "ingest.staging.delete"]
        assert all(e["actor"] == {"user": "analyst", "via": "edge"} for e in operation_events(audit_stream))
    finally:
        jobs.shutdown()


def test_ingest_then_resolve_failure_is_generic_and_logged(
    migrated_driver: Driver,
    audit_trail: AuditTrail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolve_all crash surfaces a static resolution_error; detail goes to logs only.

    result is returned verbatim through JobStatusOut.result (GET
    /ingestion/jobs/{id}), so it is client-visible state and must not carry
    exception text.
    """
    from chorus.api.routers import ingestion as ing

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(_MARKER)

    monkeypatch.setattr(ing, "resolve_all", _boom)

    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            data={"then_resolve": "true"},
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 202, resp.text
        done = _await_job(client, resp.json()["job_id"])
        assert done["status"] == "done", done
        assert done["result"]["resolution_error"] == "Resolution failed."
        assert _MARKER not in str(done)
        assert any(_MARKER in r for r in records)
    finally:
        logger.remove(sink_id)
        jobs.shutdown()


def test_resolve_runs_job_and_self_audits(
    migrated_driver: Driver,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /ingestion/resolve resolves aliases; resolve_all records the run, the route adds nothing."""
    from chorus.inference import provider

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [_unit_vector() for _ in texts])
    with migrated_driver.session() as s:
        s.run("MERGE (:Alias {surface_form: 'Bratwurst'})")

    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, audit_trail, jobs))
        resp = client.post(
            "/ingestion/resolve",
            headers={
                "X-Auth-User": "analyst",
                "X-Request-ID": "req-0001",
                "X-Audit-Purpose": encode_purpose("case-0001", None),
            },
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["kind"] == "resolve"

        done = _await_job(client, resp.json()["job_id"])
        assert done["status"] == "done", done
        assert done["result"]["resolution"]["processed"] >= 1

        assert _ops(audit_stream) == ["resolution.run", "resolution.embed"]
        for event in operation_events(audit_stream):  # the job writes with the request's caller
            assert event["actor"] == {"user": "analyst", "via": "edge"}
            assert event["request_id"] == "req-0001"
            assert event["purpose"] == {"case_ref": "case-0001"}
    finally:
        jobs.shutdown()


@pytest.mark.parametrize(
    ("method", "path", "required", "operation", "upload"),
    [
        ("post", "/ingestion/ingest", "collect", "ingest.upload", True),
        ("post", "/ingestion/resolve", "combine", "resolution.run", False),
        ("post", "/ingestion/migrate", "admin", "migration.apply", False),
    ],
)
def test_action_routes_428_without_a_purpose(
    driver: Driver,
    chorus_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    required: str,
    operation: str,
    upload: bool,
) -> None:
    """With the route's action required and no purpose stated: 428, one denied event, nothing staged."""
    from chorus.api.errors import install_error_handlers

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    stream = io.StringIO()
    trail = started_trail(stream, required=frozenset({required}))
    jobs = JobRegistry()
    try:
        app = _build_app(driver, trail, jobs)
        install_error_handlers(app)
        kwargs: dict[str, Any] = {"headers": {"X-Auth-User": "analyst"}}
        if upload:
            kwargs["files"] = [("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))]
        resp = TestClient(app).request(method, path, **kwargs)
        assert resp.status_code == 428, resp.text
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()
        trail.close()

    [event] = operation_events(stream)
    assert (event["action"], event["operation"], event["outcome"]) == (required, operation, "denied")
    assert "target" not in event
    assert_chain_ok(stream)
    uploads = chorus_env / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_job_audit_failure_marks_the_job_failed(
    migrated_driver: Driver, chorus_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job whose collection cannot be recorded ends 'error'; the staged copy is still removed."""
    import atexit

    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setenv("NER_ENABLED", "false")
    stream = io.StringIO()
    trail = started_trail(stream)
    stream.close()
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 202
        done = _await_job(client, resp.json()["job_id"])
        assert done["status"] == "error"
        assert done["result"] is None
    finally:
        jobs.shutdown()
        atexit.unregister(trail.emitter.close)  # the broken chain cannot write its shutdown event
    uploads = chorus_env / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_resolve_409_when_a_job_is_active(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """A resolve is rejected while a job is in flight."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    started, release = threading.Event(), threading.Event()

    def _block(_job: Job) -> dict[str, Any]:
        started.set()
        release.wait(timeout=5.0)
        return {"ok": True}

    try:
        jobs.submit("ingest", _block)
        assert started.wait(timeout=5.0)
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post("/ingestion/resolve", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 409
    finally:
        release.set()
        jobs.shutdown()


def test_job_status_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch, audit_trail: AuditTrail) -> None:
    """An unknown job id is a 404."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.get("/ingestion/jobs/job-999", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 404
    finally:
        jobs.shutdown()


@pytest.mark.parametrize(
    ("filename", "data", "expected"),
    [
        (f"{_MARKER}.csv", {}, "rejected 1 upload(s) with an unrecognized or unsafe filename"),
        ("postings.csv", {"since": _MARKER}, "'since' is not an ISO-8601 timestamp"),
    ],
)
def test_ingest_rejection_log_omits_submitted_values(
    monkeypatch: pytest.MonkeyPatch, filename: str, data: dict[str, str], expected: str, audit_trail: AuditTrail
) -> None:
    """A rejected upload name or 'since' value is never echoed into the log."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, audit_trail, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", (filename, _POSTINGS_CSV, "text/csv"))],
            data=data,
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 422
    finally:
        logger.remove(sink_id)
        jobs.shutdown()

    logged = "\n".join(records)
    assert expected in logged
    assert _MARKER not in logged
