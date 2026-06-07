"""/ingestion/* router: feature gate, feature status, migrations, migrate.

Gate and feature-status cases need no Neo4j and run fast; migrations/migrate
cases use the driver fixtures (and so the session Neo4j testcontainer). The
worker-backed ingest/resolve routes are covered in later commits.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Driver

from chorus.ingestion.jobs import Job, JobRegistry

_POSTINGS_CSV = (
    b"UUID,Posting ID,Text Content,Timestamp,Crawled at,Author ID,Author,Network,Tags\r\n"
    b'p-1,post-net-1,hello berlin,2026-05-01T10:00:00+00:00,2026-05-02T10:00:00+00:00,'
    b'a-1,Alice,linkedin,"news, politics"\r\n'
)


def _await_job(client: TestClient, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    """Poll GET /ingestion/jobs/{id} until the job is terminal or time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/ingestion/jobs/{job_id}", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("done", "error"):
            return body
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


def _audit_rows(audit: Any) -> list[dict[str, Any]]:
    """Read all audit rows from the logger's SQLite file, oldest first."""
    conn = sqlite3.connect(audit.db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Feature gate + status (no Neo4j)
# --------------------------------------------------------------------------


def test_feature_reports_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /ingestion/feature answers even when the flag is off."""
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, None, jobs)).get(
            "/ingestion/feature", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False}
    finally:
        jobs.shutdown()


def test_feature_reports_enabled_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /ingestion/feature reflects INGESTION_UI_ENABLED=true."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, None, jobs)).get(
            "/ingestion/feature", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True}
    finally:
        jobs.shutdown()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/ingestion/migrations"),
        ("post", "/ingestion/migrate"),
        ("get", "/ingestion/jobs/job-1"),
    ],
)
def test_gated_routes_403_when_disabled(monkeypatch: pytest.MonkeyPatch, method: str, path: str) -> None:
    """Action/data routes are forbidden when the feature flag is off."""
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, None, jobs)).request(method, path, headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 403
    finally:
        jobs.shutdown()


def test_gated_route_401_without_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gated route requires authentication even when enabled."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.delenv("CHORUS_DEFAULT_IDENTITY", raising=False)
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(None, None, jobs)).get("/ingestion/migrations")
        assert resp.status_code == 401
    finally:
        jobs.shutdown()


# --------------------------------------------------------------------------
# Migrations status + apply (Neo4j)
# --------------------------------------------------------------------------


def test_migrations_status_on_fresh_db(driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a fresh DB, everything is pending and nothing is applied."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(driver, None, jobs)).get(
            "/ingestion/migrations", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] == []
        assert body["pending"], "expected pending migrations on a fresh DB"
    finally:
        jobs.shutdown()


def test_migrations_status_after_migrate(migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a migrated DB, nothing is pending and versions are applied."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(migrated_driver, None, jobs)).get(
            "/ingestion/migrations", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"], "expected applied migrations after migrate"
        assert body["pending"] == []
    finally:
        jobs.shutdown()


def test_migrate_applies_on_fresh_db_and_audits(
    driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /ingestion/migrate applies pending migrations and writes one audit row."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(driver, in_memory_audit, jobs)).post(
            "/ingestion/migrate", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json()["applied"], "expected freshly applied versions"

        rows = [r for r in _audit_rows(in_memory_audit) if r["tool_name"] == "migrate"]
        assert len(rows) == 1
        assert rows[0]["user"] == "analyst"
        assert rows[0]["status"] == "ok"
        assert rows[0]["result_count"] >= 1
    finally:
        jobs.shutdown()


def test_migrate_noop_on_migrated_db_audits_zero(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migrate with nothing pending returns [] and audits result_count 0."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        resp = TestClient(_build_app(migrated_driver, in_memory_audit, jobs)).post(
            "/ingestion/migrate", headers={"X-Auth-User": "analyst"}
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] == []

        rows = [r for r in _audit_rows(in_memory_audit) if r["tool_name"] == "migrate"]
        assert len(rows) == 1
        assert rows[0]["result_count"] == 0
    finally:
        jobs.shutdown()


def test_migrate_409_when_a_job_is_active(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
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
        resp = TestClient(_build_app(migrated_driver, in_memory_audit, jobs)).post(
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
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uploading a valid postings.csv runs an ingest job to done and audits it."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    monkeypatch.setenv("NER_ENABLED", "false")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, in_memory_audit, jobs))
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
            n = s.run("MATCH (p:Post:Posting {uuid:'p-1'}) RETURN count(p) AS n").single()["n"]
        assert n == 1

        rows = [r for r in _audit_rows(in_memory_audit) if r["tool_name"] == "ingest"]
        assert len(rows) == 1
        assert rows[0]["user"] == "analyst"
        assert rows[0]["status"] == "ok"
        assert rows[0]["result_count"] >= 1
    finally:
        jobs.shutdown()


def test_ingest_rejects_unrecognized_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mis-named upload is 422'd and no job is created (no silent 0-row run)."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, None, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("data.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 422
        assert "data.csv" in resp.text
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()


@pytest.mark.parametrize("evil", ["../postings.csv", "sub/postings.csv", "..\\postings.csv"])
def test_ingest_rejects_path_traversal_filename(monkeypatch: pytest.MonkeyPatch, evil: str) -> None:
    """A filename carrying a path or '..' is 422'd and never staged or run."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, None, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", (evil, _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 422, resp.text
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()


def test_ingest_rejects_bad_since(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unparseable 'since' is 422'd before any job is created."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, None, jobs))
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


def test_ingest_requires_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Posting with no files is rejected."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, None, jobs))
        resp = client.post("/ingestion/ingest", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 422
    finally:
        jobs.shutdown()


def test_ingest_403_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid upload is still forbidden when the feature flag is off."""
    monkeypatch.delenv("INGESTION_UI_ENABLED", raising=False)
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, None, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 403
        assert jobs.get("job-1") is None
    finally:
        jobs.shutdown()


def test_ingest_409_when_a_job_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
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
        client = TestClient(_build_app(None, None, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 409
    finally:
        release.set()
        jobs.shutdown()


def test_ingest_error_marks_job_error_and_cleans_staging(
    migrated_driver: Driver, in_memory_audit: Any, chorus_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If run_once raises, the job ends 'error', audits an error row, and removes staging."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    from chorus.api.routers import ingestion as ing

    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ing, "run_once", _boom)

    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(migrated_driver, in_memory_audit, jobs))
        resp = client.post(
            "/ingestion/ingest",
            files=[("files", ("postings.csv", _POSTINGS_CSV, "text/csv"))],
            headers={"X-Auth-User": "analyst"},
        )
        assert resp.status_code == 202
        done = _await_job(client, resp.json()["job_id"])
        assert done["status"] == "error"
        assert "disk full" in done["error"]

        rows = [r for r in _audit_rows(in_memory_audit) if r["tool_name"] == "ingest"]
        assert len(rows) == 1
        assert rows[0]["status"] == "error"

        uploads = chorus_env / "uploads"
        leftovers = list(uploads.iterdir()) if uploads.exists() else []
        assert leftovers == [], f"staging not cleaned: {leftovers}"
    finally:
        jobs.shutdown()


def test_job_status_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown job id is a 404."""
    monkeypatch.setenv("INGESTION_UI_ENABLED", "true")
    jobs = JobRegistry()
    try:
        client = TestClient(_build_app(None, None, jobs))
        resp = client.get("/ingestion/jobs/job-999", headers={"X-Auth-User": "analyst"})
        assert resp.status_code == 404
    finally:
        jobs.shutdown()
