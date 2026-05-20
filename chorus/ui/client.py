"""Thin httpx wrapper for the chorus FastAPI surface.

Used by the Streamlit UI; production-grade callers should construct
their own client with proper auth.
"""

from __future__ import annotations

from typing import Any

import httpx


class ChorusClient:
    def __init__(self, base_url: str, identity: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"X-Auth-User": identity},
        )

    def health(self) -> dict[str, Any]:
        r = self._client.get("/health")
        r.raise_for_status()
        return r.json()

    def list_tools(self) -> list[dict[str, Any]]:
        r = self._client.get("/tools")
        r.raise_for_status()
        return r.json()

    def call_tool(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(f"/tools/{name}", json=payload)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()
