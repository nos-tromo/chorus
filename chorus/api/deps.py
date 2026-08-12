"""Shared request-scoped dependencies (ADR 0017).

``resolve_context`` bundles the authenticated principal, the validated
active project, and that project's driver + audit logger into one object
so routers cannot accidentally pair project A's driver with project B's
audit log.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from neo4j import Driver

from chorus.api.auth.principal import resolve_principal, resolve_project
from chorus.audit.logger import AuditLogger


@dataclass(frozen=True)
class RequestContext:
    """Everything a project-scoped route handler needs.

    Attributes:
        user: Authenticated identity (from :func:`resolve_principal`).
        project: Validated active project (from :func:`resolve_project`).
        driver: Neo4j driver bound to the active project's instance.
        audit: Audit logger bound to the active project's audit DB.
    """

    user: str
    project: str
    driver: Driver
    audit: AuditLogger


def resolve_context(
    request: Request,
    user: str = Depends(resolve_principal),
    project: str = Depends(resolve_project),
) -> RequestContext:
    """Resolve the per-request context from the app's project registries.

    Args:
        request: The active FastAPI request; ``app.state.drivers`` and
            ``app.state.audits`` are populated by the lifespan handler.
        user: Authenticated principal (dependency-injected).
        project: Validated active project (dependency-injected).

    Returns:
        A populated :class:`RequestContext` for the active project.
    """
    return RequestContext(
        user=user,
        project=project,
        driver=request.app.state.drivers[project],
        audit=request.app.state.audits[project],
    )
