"""Unit tests for the inference provider.

The provider is the only place that knows about provider specifics, so
the test surface here is small: confirm `extract_entities` sends the
configured `NER_MODEL` in the request body.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest


def _make_fake_response(content: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_extract_entities_uses_ner_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NER_MODEL", "my-test-ner-model")

    # Reload so the lru_cache picks up the new env.
    import sys

    for m in ("chorus.utils.env_cfg", "chorus.inference.provider"):
        if m in sys.modules:
            del sys.modules[m]
    from chorus.inference import provider

    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_fake_response(
            json.dumps(
                [
                    {
                        "text": "Berlin",
                        "label": "LOC",
                        "start": 0,
                        "end": 6,
                        "confidence": 0.95,
                    }
                ]
            )
        )

    monkeypatch.setattr(provider._client().chat.completions, "create", fake_create)

    spans = provider.extract_entities("Berlin is great", labels=["LOC"], threshold=0.5)

    assert captured["model"] == "my-test-ner-model"
    assert captured["extra_body"]["gliner_labels"] == ["LOC"]
    assert captured["extra_body"]["gliner_threshold"] == 0.5
    assert len(spans) == 1
    assert spans[0].text == "Berlin"
    assert spans[0].label == "LOC"


def test_chat_returns_assistant_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from chorus.inference import provider

    monkeypatch.setattr(
        provider._client().chat.completions,
        "create",
        lambda **kw: _make_fake_response("hello back"),
    )
    out = provider.chat([{"role": "user", "content": "hi"}])
    assert out == "hello back"


def test_embed_returns_one_vector_per_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from chorus.inference import provider

    fake = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in range(3)]
    )
    monkeypatch.setattr(provider._client().embeddings, "create", lambda **kw: fake)
    out = provider.embed(["a", "b", "c"])
    assert len(out) == 3
    assert out[0] == [0.1, 0.2, 0.3]
