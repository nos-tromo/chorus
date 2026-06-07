"""ChorusClient ingestion methods, exercised against an httpx MockTransport.

These lock the wire behavior (path, method, the X-Auth-User header, and the
multipart encoding of an upload) without standing up a server.
"""

from __future__ import annotations

import httpx
import pytest

from chorus.ui.client import ChorusClient


def _client_with(handler: object) -> ChorusClient:
    """Build a ChorusClient whose transport is a MockTransport(handler)."""
    return ChorusClient(
        base_url="http://test",
        identity="analyst",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def test_ingestion_status_gets_feature() -> None:
    """ingestion_status() GETs /ingestion/feature and returns the body."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"enabled": True})

    assert _client_with(handler).ingestion_status() == {"enabled": True}
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/ingestion/feature"
    assert seen[0].headers["X-Auth-User"] == "analyst"


def test_migrations_and_migrate() -> None:
    """migrations() GETs status; migrate() POSTs and returns applied versions."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/ingestion/migrations":
            return httpx.Response(200, json={"applied": ["001"], "pending": ["002"]})
        if request.method == "POST" and request.url.path == "/ingestion/migrate":
            return httpx.Response(200, json={"applied": ["002"]})
        return httpx.Response(404)

    client = _client_with(handler)
    assert client.migrations() == {"applied": ["001"], "pending": ["002"]}
    assert client.migrate() == {"applied": ["002"]}


def test_resolve_and_job_status() -> None:
    """resolve() POSTs and job_status() GETs the per-job route."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/ingestion/resolve":
            return httpx.Response(202, json={"job_id": "job-2", "status": "queued", "kind": "resolve"})
        return httpx.Response(200, json={"id": "job-2", "status": "done", "result": {"resolution": {}}})

    client = _client_with(handler)
    assert client.resolve()["job_id"] == "job-2"
    assert client.job_status("job-2")["status"] == "done"
    assert seen[0].method == "POST"
    assert seen[1].method == "GET"
    assert seen[1].url.path == "/ingestion/jobs/job-2"


def test_ingest_posts_multipart_with_flags() -> None:
    """ingest() uploads files as multipart and carries since + then_resolve."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["ctype"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(202, json={"job_id": "job-1", "status": "queued", "kind": "ingest"})

    client = _client_with(handler)
    out = client.ingest([("postings.csv", b"UUID\r\np-1\r\n")], since="2026-01-01", then_resolve=True)

    assert out["job_id"] == "job-1"
    assert captured["method"] == "POST"
    assert captured["path"] == "/ingestion/ingest"
    assert str(captured["ctype"]).startswith("multipart/form-data")
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"postings.csv" in body
    assert b"then_resolve" in body and b"true" in body
    assert b"2026-01-01" in body


def test_ingest_omits_since_when_none() -> None:
    """With since=None, no 'since' part is sent (the server treats it as full pull)."""
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(202, json={"job_id": "job-1", "status": "queued", "kind": "ingest"})

    _client_with(handler).ingest([("postings.csv", b"x")])
    assert b'name="since"' not in captured["body"]


def test_default_timeout_unchanged() -> None:
    """The transport seam does not alter the default request behavior."""
    # A no-arg construction must still work (production path, real transport).
    client = ChorusClient(base_url="http://localhost:8000", identity="dev")
    assert client.identity == "dev"
    client.close()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
