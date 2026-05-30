"""provider.chat_message returns the raw assistant message and forwards tools."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest


def test_chat_message_forwards_tools_and_returns_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_message forwards model/tools/tool_choice and returns the message object."""
    from chorus.inference import provider

    captured: dict[str, Any] = {}

    class _Msg:
        content = "hi"
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices: ClassVar[list[_Choice]] = [_Choice()]

    class _Completions:
        def create(self, **kwargs: Any) -> _Resp:
            captured.update(kwargs)
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        chat = _Chat()

    monkeypatch.setattr(provider, "_client", lambda: _FakeClient())

    tools = [{"type": "function", "function": {"name": "t"}}]
    msg = provider.chat_message(
        [{"role": "user", "content": "hello"}],
        model="test-model",
        tools=tools,
        tool_choice="auto",
    )
    assert msg.content == "hi"
    assert captured["model"] == "test-model"
    assert captured["tools"] == tools
    assert captured["tool_choice"] == "auto"
