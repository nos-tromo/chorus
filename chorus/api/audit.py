"""Request-scoped audit context: who asks, for what stated purpose, under which id.

Every audited route depends on :func:`request_audit`. It runs after the
principal seam (so an unauthenticated request is a 401 and is never
audited), decodes ``X-Audit-Purpose`` (a malformed value is a 400 and emits
nothing: there is no valid operation yet), keeps ``X-Request-ID`` only when
it is a sane correlation id, and binds the process's audit trail
(``app.state.audit``) to that caller.

The purpose is personal data in its own right: it goes into audit events
only, never into the operational log.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from loguru import logger

from chorus.api.auth.principal import resolve_actor
from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor, Purpose
from chorus.utils.audit_runtime import (
    PURPOSE_HEADER,
    REQUEST_ID_HEADER,
    PurposeError,
    clean_request_id,
    parse_purpose_header,
)


def request_audit(
    request: Request,
    actor: Actor = Depends(resolve_actor),  # noqa: B008 — FastAPI dependency marker
) -> AuditContext:
    """Bind the audit trail to the caller of an audited request.

    Args:
        request: The active request; supplies the headers and the trail on
            ``app.state.audit``.
        actor: The authenticated request's audit actor.

    Returns:
        An :class:`~chorus.audit.trail.AuditContext` carrying the actor, the
        stated purpose (``None`` when none was stated) and the request id.

    Raises:
        HTTPException: ``400`` when ``X-Audit-Purpose`` is malformed,
            oversized or ambiguous.
    """
    try:
        case_ref, reason = parse_purpose_header(request.headers.get(PURPOSE_HEADER))
    except PurposeError as exc:
        logger.warning("rejected a malformed {} header on {} {}", PURPOSE_HEADER, request.method, request.url.path)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid request.") from exc
    purpose = Purpose(case_ref=case_ref, reason=reason) if case_ref or reason else None
    trail: AuditTrail = request.app.state.audit
    return trail.context(
        actor,
        purpose=purpose,
        request_id=clean_request_id(request.headers.get(REQUEST_ID_HEADER)),
    )
