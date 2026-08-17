"""Ingestion-router project scoping tests (ADR 0017)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import Driver

from chorus.ingestion.jobs import JobRegistry

_POSTINGS_CSV = (
    "UUID,Posting ID,URL,Date last updated,Timestamp,Timezone,Crawled at,"
    "Postings Connections,Network Posting ID,Location,Author ID,Author,"
    "Vanity Name,Co-Author,Quoted User,Expected Reactions,Collected Reactions,"
    "Expected Comments,Collected Comments,Network,Posted in Group,Task,"
    "Text Content,Filename,Tags\n"
    "p-proj-1,1,,,2026-01-01T00:00:00,,2026-01-02T00:00:00,,,,a1,Test Author,"
    ",,,,,,,examplenet,,,hello project world,,\n"
)


def _build_app(driver: Any, audit: Any, jobs: JobRegistry) -> FastAPI:
    from chorus.api.routers import ingestion as ingestion_router

    app = FastAPI()
    app.include_router(ingestion_router.status_router)
    app.include_router(ingestion_router.router)
    app.state.drivers = {"default": driver}
    app.state.audits = {"default": audit}
    app.state.jobs = jobs
    return app


def _await_job(client: TestClient, job_id: str, timeout_s: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/ingestion/jobs/{job_id}", headers={"X-Auth-User": "analyst"}).json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_ingest_job_is_project_scoped(
    migrated_driver: Driver,
    in_memory_audit: Any,
    monkeypatch: pytest.MonkeyPatch,
    chorus_env: Path,
) -> None:
    """An ingest job's state is fully scoped to the active project.

    Uploads stage under the project dir, the raw store lands there too,
    and the job + audit row report the project.
    """
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

        done = _await_job(client, accepted["job_id"])
        assert done["status"] == "done", done
        assert done["project"] == "default"

        project_root = chorus_env / "projects" / "default"
        assert (project_root / "uploads").exists()
        assert (project_root / "raw.sqlite").exists()

        conn = sqlite3.connect(in_memory_audit.db_path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE tool_name = 'ingest'")]
        conn.close()
        assert len(rows) == 1
        assert rows[0]["project"] == "default"
    finally:
        jobs.shutdown()
