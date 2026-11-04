"""The correlation id of the operation being served, forwarded to inference.

A request's cleaned ``X-Request-ID`` (see ``chorus.api.correlation``) is held
in a context variable for as long as the request — or the background job it
started — runs. Every HTTP client that talks to the inference router installs
:func:`forward_request_id` as a request hook, so each model call carries the
same id and can be matched to its audit events. Nothing else is forwarded:
never the user identity, never the stated purpose.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final, Protocol

from chorus.utils.audit_runtime import REQUEST_ID_HEADER

#: The cleaned correlation id of the request or job being served, if any.
REQUEST_ID: Final[ContextVar[str | None]] = ContextVar("chorus_request_id", default=None)


class _OutgoingRequest(Protocol):
    """What the hook needs of a request: ``httpx`` and the OpenAI SDK's ``httpx2`` both fit."""

    @property
    def headers(self) -> MutableMapping[str, str]:
        """The request headers."""
        ...


def forward_request_id(request: _OutgoingRequest) -> None:
    """Add the current correlation id to an outgoing inference request.

    A request event hook for ``httpx`` clients (and the OpenAI SDK's
    ``httpx2`` client). A header the caller set explicitly is left as it is.

    Args:
        request: The request about to be sent.
    """
    request_id = REQUEST_ID.get()
    if request_id and REQUEST_ID_HEADER not in request.headers:
        request.headers[REQUEST_ID_HEADER] = request_id


@contextmanager
def request_id_scope(request_id: str | None) -> Iterator[None]:
    """Make ``request_id`` the current correlation id for the enclosed block.

    Background jobs run on a worker thread, which does not inherit the
    request's context; a job enters this scope with its request's id.

    Args:
        request_id: The cleaned correlation id, or ``None``.

    Yields:
        Nothing; the previous id is restored on exit.
    """
    token = REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        REQUEST_ID.reset(token)
