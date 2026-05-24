"""Unit tests for the inference provider.

The provider is the only place that knows about OpenAI-protocol
specifics, so the test surface here is small: confirm `chat` and
`embed` shape responses correctly. NER lives in
:mod:`chorus.inference.ner_client` and is tested there.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def _make_fake_response(content: str) -> Any:
    """Build a duck-typed OpenAI-shaped response wrapping ``content``.

    Args:
        content: The assistant message content the fake should return.

    Returns:
        An object with the minimal ``.choices[0].message.content``
        attribute chain :mod:`chorus.inference.provider` reads.
    """
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_chat_returns_assistant_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """``chat`` extracts the assistant message content from the response.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    from chorus.inference import provider

    monkeypatch.setattr(
        provider._client().chat.completions,
        "create",
        lambda **kw: _make_fake_response("hello back"),
    )
    out = provider.chat([{"role": "user", "content": "hi"}])
    assert out == "hello back"


def test_embed_returns_one_vector_per_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """``embed`` yields one vector per input text, in order.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    from chorus.inference import provider

    fake = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in range(3)]
    )
    monkeypatch.setattr(provider._client().embeddings, "create", lambda **kw: fake)
    out = provider.embed(["a", "b", "c"])
    assert len(out) == 3
    assert out[0] == [0.1, 0.2, 0.3]
