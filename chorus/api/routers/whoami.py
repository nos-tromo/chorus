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

from chorus.api.auth.principal import resolve_principal
from chorus.utils.env_cfg import load_principal_env

router = APIRouter(tags=["whoami"])


class WhoamiOut(BaseModel):
    """The caller's resolved identity.

    Attributes:
        username: The authenticated principal (trusted-header identity
            or the dev-only ``CHORUS_DEFAULT_IDENTITY`` fallback).
        display_name: The decorative Authelia displayname from
            ``X-Auth-Name``, when the gateway sends it. Never an
            identity key — UI display only.
    """

    username: str
    display_name: str | None


@router.get("/whoami", response_model=WhoamiOut)
def get_whoami(request: Request, principal: str = Depends(resolve_principal)) -> WhoamiOut:
    """Return the resolved calling identity, for the SPA's AppHeader.

    ``display_name`` reads the raw ``X-Auth-Name`` header directly (it is
    decorative, not part of the trusted-principal seam) rather than going
    through ``resolve_principal``.

    Args:
        request: The active FastAPI request.
        principal: The resolved request principal (401s closed).

    Returns:
        WhoamiOut: The caller's username and, when present, display name.
    """
    display_header = load_principal_env().display_name_header
    return WhoamiOut(username=principal, display_name=request.headers.get(display_header))
