"""Trusted-header principal seam.

In v1 chorus reads the authenticated principal from trusted headers that
the edge-plane gateway sets after Authelia forward-auth. When OIDC is wired
up in-process, only this module changes — its callers keep the same
contract.

The backend also sits on the shared ``inference-net`` and ``data-net``
networks, where any container can call it directly. With
``CHORUS_EDGE_SECRET`` set, the identity headers count only on requests
whose ``X-Edge-Secret`` matches it, a value only the gateway sends; on any
other request they are ignored as if absent. Unset, they are trusted from
every caller.

`CHORUS_DEFAULT_IDENTITY` exists as a dev-only fallback. Production should
leave it unset so missing headers fail closed.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from loguru import logger

from chorus.utils.env_cfg import load_principal_env

EDGE_SECRET_HEADER = "X-Edge-Secret"


@dataclass(frozen=True)
class IdentityHeaders:
    """The identity header values a request is trusted for.

    Both fields are ``None`` when the request failed the edge-secret
    check, whatever it sent.

    Attributes:
        user: Value of the identity header (``CHORUS_AUTH_HEADER``).
        display_name: Value of the decorative display-name header
            (``CHORUS_DISPLAY_NAME_HEADER``). UI display only, never an
            identity key.
    """

    user: str | None
    display_name: str | None


def check_principal_config() -> None:
    """Validate the principal configuration at application startup.

    Loading the config raises on a too-short ``CHORUS_EDGE_SECRET``, so a
    misconfigured backend stops before it serves anything. When neither
    the edge secret nor a default identity is set, one warning records
    that the identity headers are trusted from any caller.

    Raises:
        RuntimeError: If ``CHORUS_EDGE_SECRET`` is set but too short.
    """
    cfg = load_principal_env()
    if cfg.edge_secret is None and cfg.default_identity is None:
        logger.warning(
            "identity headers are trusted from any caller that can reach the backend; "
            "set CHORUS_EDGE_SECRET (see edge-plane docs/identity-contract.md)"
        )


def _edge_verified(request: Request, secret: str) -> bool:
    """Report whether the request carries the gateway's edge secret.

    Args:
        request: The active FastAPI request.
        secret: The configured ``CHORUS_EDGE_SECRET``.

    Returns:
        ``True`` only when ``X-Edge-Secret`` is present and equal to
        ``secret``, compared in constant time.
    """
    received = request.headers.get(EDGE_SECRET_HEADER)
    if received is None:
        return False
    return hmac.compare_digest(received.encode(), secret.encode())


def resolve_identity_headers(request: Request) -> IdentityHeaders:
    """Return the identity headers the request is trusted for.

    Without ``CHORUS_EDGE_SECRET`` the headers are taken as sent. With it,
    they count only when ``X-Edge-Secret`` matches; otherwise they are
    ignored, and a warning names the method and path — never a header
    value or the query string.

    FastAPI caches dependencies per request, so a route that also depends
    on :func:`resolve_principal` runs this check (and warns) once.

    Args:
        request: The active FastAPI request.

    Returns:
        The trusted :class:`IdentityHeaders`; both fields ``None`` when the
        request failed the edge-secret check.
    """
    cfg = load_principal_env()
    sent = IdentityHeaders(
        user=request.headers.get(cfg.header_name),
        display_name=request.headers.get(cfg.display_name_header),
    )
    if cfg.edge_secret is None or _edge_verified(request, cfg.edge_secret):
        return sent
    if sent.user or sent.display_name:
        logger.warning(
            "identity headers ignored: missing or invalid edge secret on {} {}",
            request.method,
            request.url.path,
        )
    return IdentityHeaders(user=None, display_name=None)


def resolve_principal(
    identity: IdentityHeaders = Depends(resolve_identity_headers),  # noqa: B008 — FastAPI dependency marker
) -> str:
    """Return the authenticated principal for an incoming request.

    Takes the identity header that :func:`resolve_identity_headers`
    trusts. Falls back to ``CHORUS_DEFAULT_IDENTITY`` only when that env
    var is set — production deployments leave it unset, which makes a
    missing or ignored header fail closed with ``401``.

    Args:
        identity: The request's trusted identity headers.

    Returns:
        The authenticated user identity string.

    Raises:
        HTTPException: ``401 Unauthorized`` when neither a trusted header
            nor a fallback identity is available.
    """
    if identity.user:
        return identity.user
    default_identity = load_principal_env().default_identity
    if default_identity:
        return default_identity
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authenticated principal.",
        headers={"WWW-Authenticate": "Bearer"},
    )
