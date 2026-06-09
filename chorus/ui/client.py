"""Thin httpx wrapper for the chorus FastAPI surface.

Used by the Streamlit UI; production-grade callers should construct
their own client with proper auth.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from chorus.utils.env_cfg import load_ui_env


class ChorusClient:
    """HTTP client for chorus's FastAPI surface.

    Holds an :class:`httpx.Client` with the principal header pre-set so
    each method is a thin wrapper over a single request. Intended for
    use by the Streamlit UI in development; production callers should
    construct their own client with real auth.
    """

    def __init__(
        self,
        base_url: str,
        identity: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Construct a client bound to ``base_url`` and ``identity``.

        Args:
            base_url: Base URL of the FastAPI service. Trailing slashes
                are stripped.
            identity: Dev-mode identity to send as the ``X-Auth-User``
                header on every request.
            timeout: Per-request timeout in seconds.
            transport: Optional httpx transport, used by tests to mock the
                server; production callers leave it ``None``.
        """
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"X-Auth-User": identity},
            transport=transport,
        )

    @classmethod
    def from_env(
        cls,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> ChorusClient:
        """Construct a client from the typed UI environment config.

        Args:
            transport: Optional httpx transport, primarily for tests.

        Returns:
            A client configured from ``CHORUS_API_URL``,
            ``CHORUS_UI_IDENTITY``, and ``CHORUS_UI_TIMEOUT_S``.
        """
        cfg = load_ui_env()
        return cls(
            base_url=cfg.api_url,
            identity=cfg.identity,
            timeout=cfg.timeout_s,
            transport=transport,
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

    def agent_query(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Run the natural-language agent over a conversation.

        Args:
            messages: Visible chat turns, each with ``role`` and
                ``content``; the last should be the new user turn.

        Returns:
            The agent result as JSON: ``answer``, ``trace``, ``truncated``.

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx status.
        """
        r = self._client.post("/agent/query", json={"messages": messages})
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def ingestion_status(self) -> dict[str, Any]:
        """Call ``GET /ingestion/feature`` (ungated) and return its body.

        Returns:
            ``{"enabled": bool}`` — whether the UI ingestion feature is on.

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx status.
        """
        r = self._client.get("/ingestion/feature")
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def migrations(self) -> dict[str, Any]:
        """Call ``GET /ingestion/migrations`` and return applied/pending versions.

        Returns:
            ``{"applied": [...], "pending": [...]}``.

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx status.
        """
        r = self._client.get("/ingestion/migrations")
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def migrate(self) -> dict[str, Any]:
        """Apply pending migrations via ``POST /ingestion/migrate`` (synchronous).

        A longer timeout is used because DDL on a cold database can take well
        over the default.

        Returns:
            ``{"applied": [...]}`` — versions applied on this call.

        Raises:
            httpx.HTTPStatusError: If the service returns a non-2xx status
                (e.g. ``409`` when a job is running).
        """
        r = self._client.post("/ingestion/migrate", timeout=120.0)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def ingest(
        self,
        files: list[tuple[str, bytes]],
        *,
        since: str | None = None,
        then_resolve: bool = False,
    ) -> dict[str, Any]:
        """Upload CSV table dumps via ``POST /ingestion/ingest`` (returns a job).

        Args:
            files: ``(filename, content)`` pairs; filenames must match an
                upstream table (e.g. ``"postings.csv"``).
            since: Optional ISO-8601 cutoff; omitted entirely when ``None``.
            then_resolve: When true, chain resolution after ingestion.

        Returns:
            ``{"job_id", "status", "kind"}`` for the enqueued job.

        Raises:
            httpx.HTTPStatusError: For a non-2xx status (``422`` bad filename
                or since, ``409`` busy, ``403`` disabled).
        """
        multipart = [("files", (name, content, "text/csv")) for name, content in files]
        data: dict[str, str] = {"then_resolve": "true" if then_resolve else "false"}
        if since:
            data["since"] = since
        # The upload is sent in full before the 202 returns, so allow ample time.
        r = self._client.post("/ingestion/ingest", files=multipart, data=data, timeout=300.0)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def resolve(self) -> dict[str, Any]:
        """Start alias→entity resolution via ``POST /ingestion/resolve`` (a job).

        Returns:
            ``{"job_id", "status", "kind"}`` for the enqueued job.

        Raises:
            httpx.HTTPStatusError: For a non-2xx status (``409`` when busy).
        """
        r = self._client.post("/ingestion/resolve")
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def job_status(self, job_id: str) -> dict[str, Any]:
        """Fetch a background job's state via ``GET /ingestion/jobs/{job_id}``.

        Args:
            job_id: Identifier returned by :meth:`ingest` or :meth:`resolve`.

        Returns:
            The job state; a failed job is ``200`` with ``status="error"``
            and an ``error`` message in the body.

        Raises:
            httpx.HTTPStatusError: ``404`` for an unknown job id.
        """
        r = self._client.get(f"/ingestion/jobs/{job_id}")
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()
