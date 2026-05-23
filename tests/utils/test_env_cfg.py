"""Env-loader tests focused on ingestion configuration."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_load_ingestion_env_defaults_to_chorus_home_ingest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no override, ``source_dir`` is ``<CHORUS_HOME>/ingest``.

    Pins the default drop point so a fresh install has a predictable
    location for the operator to push table dumps.
    """
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.delenv("INGESTION_SOURCE_DIR", raising=False)
    import importlib

    import chorus.utils.env_cfg as env_cfg

    importlib.reload(env_cfg)

    cfg = env_cfg.load_ingestion_env()
    assert cfg.source_dir == tmp_path / "ingest"


def test_load_ingestion_env_honors_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``INGESTION_SOURCE_DIR`` wins over the chorus-home default."""
    explicit = tmp_path / "elsewhere"
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.setenv("INGESTION_SOURCE_DIR", str(explicit))
    import importlib

    import chorus.utils.env_cfg as env_cfg

    importlib.reload(env_cfg)

    cfg = env_cfg.load_ingestion_env()
    assert cfg.source_dir == explicit


def test_load_ingestion_env_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A ``~``-prefixed override is expanded against ``$HOME``.

    Mirrors :func:`load_path_env`'s handling of ``CHORUS_HOME``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.setenv("INGESTION_SOURCE_DIR", "~/drops")
    import importlib

    import chorus.utils.env_cfg as env_cfg

    importlib.reload(env_cfg)

    cfg = env_cfg.load_ingestion_env()
    assert cfg.source_dir == tmp_path / "drops"
