"""Trusted-header principal seam.

In v1 chorus reads the authenticated principal from a trusted header set by
the upstream reverse proxy (Nginx fronting OIDC). When OIDC is wired up
in-process, only `resolve_principal` changes — its callers keep the same
contract.

`CHORUS_DEFAULT_IDENTITY` exists as a dev-only fallback. Production should
leave it unset so missing headers fail closed.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from chorus.utils.env_cfg import load_principal_env, load_projects_env


def resolve_principal(request: Request) -> str:
    """Return the authenticated principal for an incoming request.

    Reads the trusted-header value set by the upstream reverse proxy
    (Nginx + OIDC). Falls back to ``CHORUS_DEFAULT_IDENTITY`` only when
    that env var is set — production deployments leave it unset, which
    makes a missing header fail closed with ``401``.

    Args:
        request: The active FastAPI request.

    Returns:
        The authenticated user identity string.

    Raises:
        HTTPException: ``401 Unauthorized`` when neither the trusted
            header nor a fallback identity is configured.
    """
    cfg = load_principal_env()
    header_value = request.headers.get(cfg.header_name)
    if header_value:
        return header_value
    if cfg.default_identity:
        return cfg.default_identity
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authenticated principal.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def allowed_projects(request: Request) -> list[str]:
    """Return the projects this request's principal may access (ADR 0017).

    The gateway asserts the claim as a comma-separated header
    (``X-Auth-Projects``); ``CHORUS_DEFAULT_PROJECT`` is the dev-only
    fallback. The claim is intersected with the configured project set,
    preserving configured order, so a stale IdP group can never grant
    access to an instance chorus does not know about.

    In single-project compat mode (no ``CHORUS_PROJECTS``) every
    authenticated principal may access the implicit ``default`` project.

    Args:
        request: The active FastAPI request.

    Returns:
        Project names from the claim that are also configured; may be
        empty when the claim grants nothing usable.
    """
    projects = load_projects_env()
    if not projects.explicit:
        return ["default"]
    cfg = load_principal_env()
    header_value = request.headers.get(cfg.projects_header)
    if header_value:
        claimed = {part.strip() for part in header_value.split(",") if part.strip()}
    elif cfg.default_project:
        claimed = {cfg.default_project}
    else:
        return []
    return [name for name in projects.names if name in claimed]


def resolve_project(request: Request) -> str:
    """Return the validated active project for an incoming request.

    The caller's selection (``X-Chorus-Project``) must fall inside the
    gateway-asserted claim intersected with the configured set. Failures
    are loud — serving data from a different project than the caller
    believes they selected is the cross-project confusion ADR 0017
    exists to prevent.

    Args:
        request: The active FastAPI request.

    Returns:
        The active project name.

    Raises:
        HTTPException: ``403`` when the claim is missing or the selection
            falls outside it; ``400`` when several projects are allowed
            and no selection was made.
    """
    projects = load_projects_env()
    cfg = load_principal_env()
    selected = request.headers.get(cfg.project_header)
    if not projects.explicit:
        if selected and selected != "default":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Unknown project: {selected!r}.",
            )
        return "default"
    allowed = allowed_projects(request)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing project claim.",
        )
    if selected is None and cfg.default_project in allowed:
        selected = cfg.default_project
    if selected is None:
        if len(allowed) == 1:
            return allowed[0]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active project selected.",
        )
    if selected not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Project {selected!r} is not in the caller's project claim.",
        )
    return selected
