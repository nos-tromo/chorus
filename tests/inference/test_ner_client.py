"""Unit tests for the remote GLiNER NER HTTP client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest


@pytest.fixture(autouse=True)
def _reset_env_and_caches(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear NER_* env vars and the module's lru_caches between tests.

    The ner_client module caches its config and httpx client for the
    lifetime of the process, so any env-var manipulation needs the
    caches evicted to take effect. ``monkeypatch`` rolls back the env
    automatically; we just have to drop the caches up-front and on the
    way out.
    """
    for key in ("NER_API_BASE", "NER_API_KEY", "NER_THRESHOLD", "NER_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)

    from chorus.inference import ner_client

    ner_client._config.cache_clear()
    ner_client._client.cache_clear()
    yield
    ner_client._config.cache_clear()
    ner_client._client.cache_clear()


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
) -> None:
    """Force ner_client._client() to build atop the supplied transport.

    Patches ``httpx.Client`` as referenced from inside
    :mod:`chorus.inference.ner_client` so the lru-cached client picks
    up the mock without us having to construct the real one.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        handler: An :class:`httpx.MockTransport` whose handler decides
            the response for each request.
    """
    original_client = httpx.Client

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = handler
        return original_client(*args, **kwargs)

    monkeypatch.setattr("chorus.inference.ner_client.httpx.Client", _patched_client)


def test_extract_entities_maps_gliner_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success path: GLiNER spans map to ``EntitySpan`` with offsets preserved."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "entities": [
                    {
                        "start": 0,
                        "end": 5,
                        "text": "Alice",
                        "label": "person",
                        "score": 0.97,
                    },
                    {
                        "start": 15,
                        "end": 19,
                        "text": "Acme",
                        "label": "org",
                        "score": 0.92,
                    },
                ]
            },
        )

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))
    monkeypatch.setenv("NER_API_BASE", "http://gliner-ner:8000")
    # Pin the ner-only (no-auth) shape *explicitly* rather than relying on
    # NER_API_KEY being unset. env_cfg runs load_dotenv() on its per-test
    # reload, which refills an absent NER_API_KEY from a developer's real .env
    # (e.g. NER_API_KEY=$OPENAI_API_KEY). An explicit empty value is present, so
    # load_dotenv(override=False) leaves it alone and the loader reads it as
    # no-auth — keeping the test hermetic regardless of the local .env.
    monkeypatch.setenv("NER_API_KEY", "")

    from chorus.inference import ner_client

    spans = ner_client.extract_entities("Alice works at Acme.", labels=["person", "org"])

    assert len(spans) == 2
    assert spans[0].text == "Alice"
    assert spans[0].label == "person"
    assert spans[0].start == 0
    assert spans[0].end == 5
    assert spans[0].confidence == 0.97
    assert spans[1].text == "Acme"
    assert spans[1].label == "org"

    assert captured["url"] == "http://gliner-ner:8000/gliner"
    assert captured["body"] == {
        "text": "Alice works at Acme.",
        "labels": ["person", "org"],
        "threshold": 0.3,
    }
    # Empty NER_API_KEY -> no Bearer header (ner-only deployment shape).
    assert captured["auth"] is None


def test_extract_entities_sends_bearer_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full vllm-service shape: NER_API_KEY produces a Bearer header."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"entities": []})

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))
    monkeypatch.setenv("NER_API_BASE", "http://vllm-router:4000")
    monkeypatch.setenv("NER_API_KEY", "sk-test-key")

    from chorus.inference import ner_client

    ner_client.extract_entities("any text")

    assert captured["auth"] == "Bearer sk-test-key"


def test_extract_entities_empty_api_key_omits_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NER_API_KEY=`` (empty) is treated as unset — no Authorization header."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"entities": []})

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))
    monkeypatch.setenv("NER_API_KEY", "   ")

    from chorus.inference import ner_client

    ner_client.extract_entities("any text")
    assert captured["auth"] is None


def test_extract_entities_empty_text_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only input returns ``[]`` without calling the service."""
    call_count = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"entities": []})

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))

    from chorus.inference import ner_client

    assert ner_client.extract_entities("   \n\t  ") == []
    assert call_count == 0


def test_extract_entities_network_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any httpx error during the request fails closed with ``[]``."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))

    from chorus.inference import ner_client

    assert ner_client.extract_entities("Alice met Bob.") == []


def test_extract_entities_http_5xx_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5xx from the upstream also fails closed."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))

    from chorus.inference import ner_client

    assert ner_client.extract_entities("Alice met Bob.") == []


def test_extract_entities_malformed_payload_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing or non-list ``entities`` key produces ``[]``."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))

    from chorus.inference import ner_client

    assert ner_client.extract_entities("Alice met Bob.") == []


def test_extract_entities_drops_malformed_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-entity validation skips items missing required fields."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "entities": [
                    {
                        "start": 0,
                        "end": 5,
                        "text": "Alice",
                        "label": "person",
                        "score": 0.9,
                    },
                    {"text": "Berlin", "label": "loc"},  # missing offsets/score
                    {"start": 0, "end": 5, "label": "org", "score": 0.8},  # no text
                    "not-a-dict",
                ]
            },
        )

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))

    from chorus.inference import ner_client

    spans = ner_client.extract_entities("Alice in Berlin")

    assert len(spans) == 1
    assert spans[0].text == "Alice"


def test_extract_entities_per_call_threshold_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``threshold`` argument overrides ``cfg.threshold``."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"entities": []})

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))
    monkeypatch.setenv("NER_THRESHOLD", "0.4")

    from chorus.inference import ner_client

    ner_client.extract_entities("text", threshold=0.85)

    assert captured["body"]["threshold"] == 0.85


def test_extract_entities_sends_default_labels_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``labels`` is None the request body carries DEFAULT_NER_LABELS.

    The upstream GLiNER server in vllm-service requires ``labels`` —
    omitting it yields a 500 (``KeyError: 'labels'``) — so the client
    falls back to its own default set rather than relying on a
    server-side default that does not exist.
    """
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"entities": []})

    _install_mock_transport(monkeypatch, httpx.MockTransport(_handler))

    from chorus.inference import ner_client

    ner_client.extract_entities("text")

    assert captured["body"]["labels"] == ner_client.DEFAULT_NER_LABELS
