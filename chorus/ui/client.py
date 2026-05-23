"""Thin httpx wrapper for the chorus FastAPI surface.

Used by the Streamlit UI; production-grade callers should construct
their own client with proper auth.
"""

from __future__ import annotations

from typing import Any, cast

import httpx


class ChorusClient:
    """HTTP client for chorus's FastAPI surface.

    Holds an :class:`httpx.Client` with the principal header pre-set so
    each method is a thin wrapper over a single request. Intended for
    use by the Streamlit UI in development; production callers should
    construct their own client with real auth.
    """

    def __init__(self, base_url: str, identity: str, *, timeout: float = 30.0) -> None:
        """Construct a client bound to ``base_url`` and ``identity``.

        Args:
            base_url: Base URL of the FastAPI service. Trailing slashes
                are stripped.
            identity: Dev-mode identity to send as the ``X-Auth-User``
                header on every request.
            timeout: Per-request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"X-Auth-User": identity},
        )

    def health(self) -> dict[str, Any]:
        """Call ``GET /health`` and return the parsed JSON body.

        Returns:
            The health response body (typically ``{"status": "ok"}``).

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx status.
        """
        r = self._client.get("/health")
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def list_tools(self) -> list[dict[str, Any]]:
        """Call ``GET /tools`` and return the registered tools.

        Returns:
            One dict per tool with keys ``name``, ``input_schema``,
            and ``output_schema``.

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx status.
        """
        r = self._client.get("/tools")
        r.raise_for_status()
        return cast(list[dict[str, Any]], r.json())

    def call_tool(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool via ``POST /tools/{name}``.

        Args:
            name: Registered tool name.
            payload: Body to send; must match the tool's input schema.

        Returns:
            The tool's output model as JSON.

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx
                status (e.g. ``404`` for unknown tool, ``422`` for
                validation errors).
        """
        r = self._client.post(f"/tools/{name}", json=payload)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()
