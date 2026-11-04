"""ASGI middleware that holds each request's ``X-Request-ID`` for the inference clients.

The gateway sets ``X-Request-ID`` on every request. The middleware keeps it
(cleaned: only a sane correlation id) in
:data:`chorus.inference.correlation.REQUEST_ID` while the request is served,
so the model calls it makes carry the same id as its audit events.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from chorus.inference.correlation import request_id_scope
from chorus.utils.audit_runtime import REQUEST_ID_HEADER, clean_request_id

_HEADER = REQUEST_ID_HEADER.lower().encode("latin-1")


class RequestIdMiddleware:
    """Hold the request's cleaned ``X-Request-ID`` while it is served."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the next ASGI application.

        Args:
            app: The application to call.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection with its correlation id in context.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = next((value for name, value in scope.get("headers", []) if name == _HEADER), None)
        request_id = clean_request_id(raw.decode("latin-1")) if raw is not None else None
        with request_id_scope(request_id):
            await self.app(scope, receive, send)
