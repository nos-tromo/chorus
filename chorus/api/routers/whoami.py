"""GET /whoami — signed-in identity for the SPA's AppHeader.

Principal-gated like every collection-scoped endpoint (401 without a
trusted header or a configured dev default identity) — unlike ``/config``
and ``/version``, which are deliberately unauthenticated. Not §76-audited:
it queries no content data, only echoes the resolved principal and the
decorative display-name header, matching how ``/config``/``/version``
are treated (no audit log for endpoints that touch no graph data).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from chorus.api.auth.principal import (
    allowed_projects,
    resolve_principal,
    resolve_project_lenient,
)
from chorus.utils.env_cfg import load_principal_env

router = APIRouter(tags=["whoami"])


class WhoamiOut(BaseModel):
    """The caller's resolved identity and project claim.

    Attributes:
        username: The authenticated principal (trusted-header identity
            or the dev-only ``CHORUS_DEFAULT_IDENTITY`` fallback).
        display_name: The decorative Authelia displayname from
            ``X-Auth-Name``, when the gateway sends it. Never an
            identity key — UI display only.
        projects: Projects the caller may access — the gateway-asserted
            claim intersected with the configured set (ADR 0017);
            ``["default"]`` in single-project compat mode.
        active_project: The project this request resolved to; the SPA's
            project switcher initializes from it. ``None`` when nothing
            could be resolved — several projects allowed with no
            selection, or a selection that has gone stale — which is the
            SPA's cue to prompt for one.
    """

    username: str
    display_name: str | None
    projects: list[str]
    active_project: str | None


@router.get("/whoami", response_model=WhoamiOut)
def get_whoami(
    request: Request,
    principal: str = Depends(resolve_principal),
    active_project: str | None = Depends(resolve_project_lenient),
) -> WhoamiOut:
    """Return the resolved calling identity, for the SPA's AppHeader.

    ``display_name`` reads the raw ``X-Auth-Name`` header directly (it is
    decorative, not part of the trusted-principal seam) rather than going
    through ``resolve_principal``. The project list is authenticated
    output — it must never move to the unauthenticated ``/config``.

    Args:
        request: The active FastAPI request.
        principal: The resolved request principal (401s closed).
        active_project: The validated active project, or ``None`` when it
            could not be resolved. This one endpoint resolves the project
            leniently so the SPA can bootstrap its switcher; the routes
            that serve project data all fail closed.

    Returns:
        WhoamiOut: The caller's username, display name, allowed
        projects, and active project.
    """
    display_header = load_principal_env().display_name_header
    return WhoamiOut(
        username=principal,
        display_name=request.headers.get(display_header),
        projects=allowed_projects(request),
        active_project=active_project,
    )
