"""X-Request-ID reaches the inference router, from requests and from jobs.

The OpenAI-protocol client and the GLiNER client both carry the current
correlation id on every call, and nothing else about the caller. Transports
are mocked; nothing leaves the process. All values are invented.
"""

from __future__ import annotations

import functools
import json

import httpx
import httpx2
import pytest

from chorus.inference.correlation import REQUEST_ID, forward_request_id, request_id_scope


def test_hook_adds_the_current_id_only() -> None:
    """With an id in scope the header is added; without one, nothing is."""
    with request_id_scope("req-0001"):
        inside = httpx.Request("POST", "http://inference.test/v1/x")
        forward_request_id(inside)
    outside = httpx.Request("POST", "http://inference.test/v1/x")
    forward_request_id(outside)
    assert inside.headers["X-Request-ID"] == "req-0001"
    assert "X-Request-ID" not in outside.headers
    assert REQUEST_ID.get() is None


def test_hook_keeps_an_explicit_header() -> None:
    """A header the caller set is not replaced."""
    with request_id_scope("req-0001"):
        request = httpx.Request("POST", "http://inference.test/v1/x", headers={"X-Request-ID": "explicit"})
        forward_request_id(request)
    assert request.headers["X-Request-ID"] == "explicit"


def test_openai_client_forwards_the_id_and_reports_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider.embed sends X-Request-ID (and no identity) and adds the reported token usage."""
    from chorus.inference import provider

    seen: list[httpx2.Request] = []

    def _handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = {
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.5]}],
            "model": "example-embed",
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        }
        return httpx2.Response(200, json=body)

    monkeypatch.setenv("OPENAI_API_BASE", "http://inference.test/v1")
    monkeypatch.setattr(
        provider,
        "DefaultHttpxClient",
        functools.partial(provider.DefaultHttpxClient, transport=httpx2.MockTransport(_handler)),
    )
    provider._config.cache_clear()
    provider._client.cache_clear()
    try:
        usage = provider.TokenUsage()
        with request_id_scope("req-0002"):
            vectors = provider.embed(["placeholder"], usage=usage)
    finally:
        provider._client.cache_clear()
        provider._config.cache_clear()

    assert vectors == [[0.5, 0.5]]
    [request] = seen
    assert request.headers["X-Request-ID"] == "req-0002"
    assert "X-Auth-User" not in request.headers
    assert "X-Audit-Purpose" not in request.headers
    assert usage.input_tokens == 3
    assert usage.output_tokens is None


def test_ner_client_forwards_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """ner_client.extract_entities sends X-Request-ID on its /gliner call."""
    from chorus.inference import ner_client

    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"entities": []})

    monkeypatch.setattr(
        ner_client.httpx, "Client", functools.partial(httpx.Client, transport=httpx.MockTransport(_handler))
    )
    ner_client._client.cache_clear()
    ner_client._config.cache_clear()
    try:
        with request_id_scope("req-0003"):
            assert ner_client.extract_entities("placeholder text") == []
    finally:
        ner_client._client.cache_clear()
        ner_client._config.cache_clear()

    [request] = seen
    assert request.headers["X-Request-ID"] == "req-0003"
    assert json.loads(request.content)["text"] == "placeholder text"


def test_scope_is_restored_after_an_error() -> None:
    """The previous id comes back even when the scoped block raises."""
    with request_id_scope("outer"):
        with pytest.raises(RuntimeError), request_id_scope("inner"):
            raise RuntimeError("boom")
        assert REQUEST_ID.get() == "outer"
