"""Env-loader tests for the per-project configuration (ADR 0017)."""

from __future__ import annotations

import pytest


def test_projects_unset_yields_implicit_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``CHORUS_PROJECTS`` unset, one implicit project ``default`` exists.

    This is the single-project backward-compat mode: dev and existing
    deploys keep working with the legacy flat env vars.
    """
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)

    from chorus.utils.env_cfg import load_projects_env

    cfg = load_projects_env()
    assert cfg.names == ("default",)
    assert cfg.explicit is False


def test_projects_parses_and_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CHORUS_PROJECTS`` is comma-split, trimmed, order-preserving."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha, beta ,gamma-two")

    from chorus.utils.env_cfg import load_projects_env

    cfg = load_projects_env()
    assert cfg.names == ("alpha", "beta", "gamma-two")
    assert cfg.explicit is True


def test_projects_drops_empty_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stray commas (``alpha,,beta,``) do not produce empty project names."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,,beta,")

    from chorus.utils.env_cfg import load_projects_env

    assert load_projects_env().names == ("alpha", "beta")


@pytest.mark.parametrize("bad", ["Alpha", "1alpha", "al pha", "al_pha", "-alpha", "a" * 33])
def test_projects_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """Names outside ``^[a-z][a-z0-9-]{0,31}$`` fail fast at load time.

    The name feeds env-var suffixes, filesystem paths, and HTTP headers,
    so the grammar is deliberately narrow.
    """
    monkeypatch.setenv("CHORUS_PROJECTS", f"good,{bad}")

    from chorus.utils.env_cfg import load_projects_env

    with pytest.raises(RuntimeError, match="CHORUS_PROJECTS"):
        load_projects_env()


def test_projects_rejects_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate names are a configuration error, not silently deduped."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta,alpha")

    from chorus.utils.env_cfg import load_projects_env

    with pytest.raises(RuntimeError, match="duplicate"):
        load_projects_env()
