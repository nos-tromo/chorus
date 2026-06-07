"""/ingestion/* — frontend-triggered ingestion (ADR 0014).

Lets an authenticated UI user run the ingestion pipeline end-to-end —
migrate, ingest (upload), resolve — without the CLI. The data-mutating
routes are gated behind ``INGESTION_UI_ENABLED`` (default off); the
feature-status route is intentionally ungated so the UI can tell
"disabled" apart from "unreachable".

Two routers share this module:

- ``status_router`` — authenticated but ungated; only ``GET /ingestion/feature``.
- ``router`` — authenticated **and** gated; every data/action route.

Long-running work (ingest, resolve) runs on the shared
:class:`chorus.ingestion.jobs.JobRegistry` (``app.state.jobs``) and is
polled via the job-status route; migrate is fast and synchronous. The
registry serializes heavy Neo4j writers, and migrate is rejected with
``409`` while a job is active so its DDL never interleaves an in-flight
ingest.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from chorus.api.auth.principal import resolve_principal
from chorus.ingestion.jobs import Job, JobBusyError, JobKind, JobStatus
from chorus.ingestion.orchestrator import run_once
from chorus.ingestion.raw_store import RawStore
from chorus.ingestion.resolution import resolve_all
from chorus.ingestion.upstream import TABLES, FileUpstreamAdapter, table_for_filename
from chorus.migrations.runner import applied_versions, apply_all, pending_versions
from chorus.utils.env_cfg import (
    load_ingestion_ui_env,
    load_path_env,
    load_resolution_env,
    load_retention_env,
)


def require_ingestion_ui_enabled() -> None:
    """Gate dependency: 403 unless ``INGESTION_UI_ENABLED`` is set.

    Read at request time so toggling the env var takes effect without a
    restart (and so tests can flip it per-case).

    Raises:
        HTTPException: ``403 Forbidden`` when the feature flag is off.
    """
    if not load_ingestion_ui_env().enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="The data-ingestion UI is disabled (set INGESTION_UI_ENABLED=true to enable).",
        )


def _reject_if_busy(request: Request) -> None:
    """Raise ``409`` when a background ingestion job is already active.

    Args:
        request: Active request, for ``app.state.jobs``.

    Raises:
        HTTPException: ``409 Conflict`` when a job is queued or running.
    """
    if request.app.state.jobs.active_count() >= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="An ingestion job is already running; wait for it to finish.",
        )


class FeatureStatus(BaseModel):
    """Whether the UI ingestion feature is enabled on this deployment.

    Attributes:
        enabled: Mirror of ``INGESTION_UI_ENABLED``.
    """

    enabled: bool


class MigrationStatus(BaseModel):
    """Applied and pending Neo4j migrations.

    Attributes:
        applied: Versions recorded on ``:_Migration`` nodes, sorted.
        pending: Discovered-but-unapplied versions, in apply order.
    """

    applied: list[str]
    pending: list[str]


class MigrateResult(BaseModel):
    """Outcome of a migrate call.

    Attributes:
        applied: Versions newly applied on this call (empty if up to date).
    """

    applied: list[str]


class JobAccepted(BaseModel):
    """Acknowledgement that a background job was enqueued.

    Attributes:
        job_id: Identifier to poll on ``GET /ingestion/jobs/{job_id}``.
        status: Initial job status (``"queued"``).
        kind: Job kind (``"ingest"`` or ``"resolve"``).
    """

    job_id: str
    status: JobStatus
    kind: JobKind


class JobStatusOut(BaseModel):
    """Pollable state of a background job.

    Attributes:
        id: Job identifier.
        kind: Job kind.
        status: ``queued`` / ``running`` / ``done`` / ``error``.
        created_by: Principal that submitted the job.
        created_at: ISO-8601 submission time.
        finished_at: ISO-8601 completion time; ``None`` until terminal.
        result: Job output when ``status == "done"`` (ingest counts and,
            when chained, a ``resolution`` summary).
        error: Type-prefixed message when ``status == "error"``.
    """

    id: str
    kind: JobKind
    status: JobStatus
    created_by: str
    created_at: str
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


status_router = APIRouter(prefix="/ingestion", tags=["ingestion"])
router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"],
    dependencies=[Depends(resolve_principal), Depends(require_ingestion_ui_enabled)],
)


@status_router.get("/feature")
def feature(_user: str = Depends(resolve_principal)) -> FeatureStatus:
    """Report whether the UI ingestion feature is enabled.

    Authenticated but ungated: the UI calls this on page load to decide
    whether to render the ingestion controls or a "disabled" message.

    Args:
        _user: Resolved principal (enforces authentication).

    Returns:
        A :class:`FeatureStatus`.
    """
    return FeatureStatus(enabled=load_ingestion_ui_env().enabled)


@router.get("/migrations")
def migrations_status(request: Request) -> MigrationStatus:
    """Return applied and pending Neo4j migrations.

    Args:
        request: Active request, for the shared driver on ``app.state``.

    Returns:
        A :class:`MigrationStatus`.
    """
    driver = request.app.state.driver
    return MigrationStatus(
        applied=sorted(applied_versions(driver)),
        pending=pending_versions(driver),
    )


@router.post("/migrate")
def migrate(request: Request, user: str = Depends(resolve_principal)) -> MigrateResult:
    """Apply pending Neo4j migrations (synchronous, idempotent).

    Rejected with ``409`` while a background job is active so its DDL
    cannot interleave an in-flight ingest. The call is recorded as one
    ``migrate`` audit row.

    Args:
        request: Active request, for the shared driver, audit logger, and
            job registry on ``app.state``.
        user: Resolved principal, attributed on the audit row.

    Returns:
        A :class:`MigrateResult` listing versions applied on this call.

    Raises:
        HTTPException: ``409 Conflict`` when a job is already running.
    """
    _reject_if_busy(request)
    driver = request.app.state.driver
    audit = request.app.state.audit
    with audit.time_tool(user, "migrate", {}) as slot:
        newly = apply_all(driver)
        slot.result_count = len(newly)
    return MigrateResult(applied=newly)


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
def ingest(
    request: Request,
    files: list[UploadFile] = File(...),  # noqa: B008 — FastAPI reads this marker from the default
    since: str | None = Form(default=None),
    then_resolve: bool = Form(default=False),
    user: str = Depends(resolve_principal),
) -> JobAccepted:
    """Accept uploaded CSV table dumps and run one ingestion pass as a job.

    The request only validates and stages the upload, then returns ``202``
    with a job id; the pipeline runs on the shared single-worker registry and
    is polled via ``GET /ingestion/jobs/{job_id}``. Uploaded filenames must
    match a known table (``<table>.csv`` or ``*_<table>.csv``) — anything else
    is rejected so a mis-named file cannot produce a silent zero-row run.

    Args:
        request: Active request, for the shared driver, audit logger, and
            job registry on ``app.state``.
        files: Uploaded CSV files (one or more), named per upstream table.
        since: Optional ISO-8601 cutoff; only rows newer than this are pulled.
        then_resolve: When true, run alias→entity resolution in the same job
            after ingestion succeeds (the common end-to-end path).
        user: Resolved principal, attributed on the ingest audit row.

    Returns:
        A :class:`JobAccepted` with the new job id.

    Raises:
        HTTPException: ``422`` for no files, an unrecognized filename, or an
            unparseable ``since``; ``409`` when a job is already running.
    """
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no files uploaded")
    # Reduce each upload to a bare basename and require it to (a) equal what
    # the client sent — rejecting any path component or ".." — and (b) match a
    # known table. Writing the derived basename (never the raw client string)
    # keeps the staged write provably inside the staging dir independent of
    # table_for_filename's internals: defense in depth for the write below.
    safe_names: list[str] = []
    rejected: list[str] = []
    for f in files:
        raw = f.filename or ""
        base = Path(raw).name
        if base != raw or table_for_filename(base) is None:
            rejected.append(raw or "<unnamed>")
        else:
            safe_names.append(base)
    if rejected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unrecognized or unsafe filename(s): {rejected}; expected one of {list(TABLES)} "
            "as '<table>.csv' or '*_<table>.csv'",
        )
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid 'since' timestamp: {since!r}"
            ) from exc

    _reject_if_busy(request)

    uploads_root = load_path_env().chorus_home / "uploads"
    uploads_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="chorus-upload-", dir=uploads_root))
    try:
        for upload, safe in zip(files, safe_names, strict=True):
            with (staging / safe).open("wb") as dest:
                shutil.copyfileobj(upload.file, dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    driver = request.app.state.driver
    audit = request.app.state.audit

    def _ingest_job(_job: Job) -> dict[str, Any]:
        """Run one ingestion pass over the staged files; clean up on exit."""
        try:
            adapter = FileUpstreamAdapter(staging)
            raw = RawStore(load_path_env().raw_store)
            raw.init_schema()
            params = {"since": since, "files": sorted(safe_names)}
            with audit.time_tool(user, "ingest", params) as slot:
                out = run_once(adapter, driver, raw, load_retention_env(), since=since_dt)
                slot.result_count = sum(out["counts"].values())
            if then_resolve:
                # Chain resolution after a successful ingest. resolve_all writes
                # its own audit row, so it is not wrapped in time_tool here. A
                # resolution failure must not discard the (successful) ingest
                # counts — record it alongside them instead.
                try:
                    summary = resolve_all(driver, load_resolution_env(), audit, user=user)
                    out = {**out, "resolution": summary.as_dict()}
                except Exception as exc:  # keep ingest counts; surface the resolve failure
                    out = {**out, "resolution_error": f"{type(exc).__name__}: {exc}"}
            return out
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    try:
        job = request.app.state.jobs.submit("ingest", _ingest_job, created_by=user)
    except JobBusyError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An ingestion job is already running; wait for it to finish."
        ) from exc
    return JobAccepted(job_id=job.id, status=job.status, kind=job.kind)


@router.post("/resolve", status_code=status.HTTP_202_ACCEPTED)
def resolve(request: Request, user: str = Depends(resolve_principal)) -> JobAccepted:
    """Run alias→entity resolution over the graph as a background job.

    Returns ``202`` with a job id; poll ``GET /ingestion/jobs/{job_id}`` for the
    per-method resolution summary. ``resolve_all`` writes its own
    ``resolve_all`` audit row (and none on an empty run), so this route adds no
    wrapper audit row.

    Args:
        request: Active request, for the shared driver, audit logger, and job
            registry on ``app.state``.
        user: Resolved principal, attributed on the resolution audit row.

    Returns:
        A :class:`JobAccepted` with the new job id.

    Raises:
        HTTPException: ``409`` when a job is already running.
    """
    driver = request.app.state.driver
    audit = request.app.state.audit

    def _resolve_job(_job: Job) -> dict[str, Any]:
        summary = resolve_all(driver, load_resolution_env(), audit, user=user)
        return {"resolution": summary.as_dict()}

    try:
        job = request.app.state.jobs.submit("resolve", _resolve_job, created_by=user)
    except JobBusyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An ingestion job is already running; wait for it to finish."
        ) from exc
    return JobAccepted(job_id=job.id, status=job.status, kind=job.kind)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> JobStatusOut:
    """Return the current state of a background ingestion job.

    A failed job is reported as ``200`` with ``status="error"`` and the
    message in ``error`` — the read succeeded even though the job did not.
    Only an unknown id is a ``404``.

    Args:
        job_id: The job identifier returned by an enqueue route.
        request: Active request, for the job registry on ``app.state``.

    Returns:
        A :class:`JobStatusOut`.

    Raises:
        HTTPException: ``404`` when no job is known under ``job_id``.
    """
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown job: {job_id}")
    return JobStatusOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        created_by=job.created_by,
        created_at=job.created_at,
        finished_at=job.finished_at,
        result=job.result,
        error=job.error,
    )
