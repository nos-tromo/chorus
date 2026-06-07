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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from chorus.api.auth.principal import resolve_principal
from chorus.migrations.runner import applied_versions, apply_all, pending_versions
from chorus.utils.env_cfg import load_ingestion_ui_env


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
