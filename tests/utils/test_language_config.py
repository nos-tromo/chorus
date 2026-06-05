"""Tests for the RESPONSE_LANGUAGE response-language switch."""

from __future__ import annotations

import pytest


def test_default_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """With RESPONSE_LANGUAGE unset, the language is English.

    The loader reads ``os.environ`` on every call, so ``delenv`` alone —
    without a module reload, which would re-trigger ``load_dotenv`` and
    restore a value from the local ``.env`` — exercises the default.
    """
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "en"


def test_german_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """RESPONSE_LANGUAGE=de selects German."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "de"


@pytest.mark.parametrize("raw", ["DE", "De", " de "])
def test_value_is_case_and_whitespace_insensitive(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The code is normalised: case-folded and stripped."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", raw)
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "de"


def test_unknown_value_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised language code falls back silently to English."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "fr")
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "en"
