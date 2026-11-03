"""GET /whoami — signed-in identity for the SPA's AppHeader.

Principal-gated like every collection-scoped endpoint (401 without a
trusted header or a configured dev default identity) — unlike ``/config``
and ``/version``, which are deliberately unauthenticated. Not §76-audited:
it queries no content data, only echoes the resolved principal and the
decorative display-name header, matching how ``/config``/``/version``
are treated (no audit log for endpoints that touch no graph data).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from chorus.api.auth.principal import IdentityHeaders, resolve_identity_headers, resolve_principal

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
def get_whoami(
    principal: str = Depends(resolve_principal),
    identity: IdentityHeaders = Depends(resolve_identity_headers),  # noqa: B008 — FastAPI dependency marker
) -> WhoamiOut:
    """Return the resolved calling identity, for the SPA's AppHeader.

    ``display_name`` comes from the same trusted identity headers as the
    principal, so a request that fails the edge-secret check shows none.

    Args:
        principal: The resolved request principal (401s closed).
        identity: The request's trusted identity headers.

    Returns:
        WhoamiOut: The caller's username and, when present, display name.
    """
    return WhoamiOut(username=principal, display_name=identity.display_name)
