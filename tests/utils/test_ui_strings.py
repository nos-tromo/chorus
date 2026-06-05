"""Tests for the locale-aware UI string catalog."""

from __future__ import annotations

import pytest


def test_keys_match_across_languages() -> None:
    """Every language pack exposes the same key set (import-time parity)."""
    from chorus.utils.env_cfg import SUPPORTED_LANGUAGES
    from chorus.utils.ui_strings import UI_STRINGS

    en_keys = set(UI_STRINGS["en"])
    for lang in SUPPORTED_LANGUAGES:
        assert set(UI_STRINGS[lang]) == en_keys


def test_ui_string_returns_german_under_de(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ui_string`` resolves to the German value when RESPONSE_LANGUAGE=de."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    from chorus.utils.ui_strings import ui_string

    assert ui_string("posts.no_hits") == "keine Treffer"


def test_ui_string_returns_english_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ui_string`` resolves to English when the language is unset/default."""
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    from chorus.utils.ui_strings import ui_string

    assert ui_string("posts.no_hits") == "no hits"


def test_ui_string_placeholders_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """A templated value exposes named placeholders for ``.format()``."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    from chorus.utils.ui_strings import ui_string

    assert ui_string("posts.hits").format(n=3) == "3 Treffer"
