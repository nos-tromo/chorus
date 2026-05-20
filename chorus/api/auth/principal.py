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

from chorus.utils.env_cfg import load_principal_env


def resolve_principal(request: Request) -> str:
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
