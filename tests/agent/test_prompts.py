"""Tests for system-prompt selection by language."""

from __future__ import annotations


def test_english_is_default_and_unchanged() -> None:
    """``get_system_prompt('en')`` returns the English prompt."""
    from chorus.agent.prompts import SYSTEM_PROMPT_EN, get_system_prompt

    assert get_system_prompt("en") is SYSTEM_PROMPT_EN
    assert "analytical assistant" in SYSTEM_PROMPT_EN


def test_unknown_code_falls_back_to_english() -> None:
    """An unknown language code falls back to the English prompt."""
    from chorus.agent.prompts import SYSTEM_PROMPT_EN, get_system_prompt

    assert get_system_prompt("fr") is SYSTEM_PROMPT_EN


def test_german_prompt_is_distinct_and_carries_sensitivity_rules() -> None:
    """The German prompt answers in German and encodes entity sensitivity."""
    from chorus.agent.prompts import SYSTEM_PROMPT_DE, SYSTEM_PROMPT_EN, get_system_prompt

    de = get_system_prompt("de")
    assert de is SYSTEM_PROMPT_DE
    assert de != SYSTEM_PROMPT_EN
    assert "auf Deutsch" in de  # answers in German
    assert "AfD" in de  # entity example
    assert "Artikel" in de  # article-stripping rule


def test_backcompat_alias_points_at_english() -> None:
    """The legacy ``SYSTEM_PROMPT`` name still resolves (to English)."""
    from chorus.agent.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_EN

    assert SYSTEM_PROMPT is SYSTEM_PROMPT_EN
