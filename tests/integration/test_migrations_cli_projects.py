"""Per-project migrations CLI tests (ADR 0017)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_migrate_apply_defaults_to_all_projects(chorus_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``apply`` with no flag targets every configured project (compat: default)."""
    from chorus.migrations.cli import main

    assert main(["apply"]) == 0
    out = capsys.readouterr().out
    assert "[default] applied" in out or "[default] up to date" in out

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "[default]" in out


def test_migrate_apply_single_project_flag(
    chorus_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    neo4j_container: tuple[str, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--project`` narrows the run to the named project."""
    uri, _user, _password = neo4j_container
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.setenv("NEO4J_URI_ALPHA", uri)
    monkeypatch.setenv("NEO4J_URI_BETA", uri)

    from chorus.migrations.cli import main

    assert main(["apply", "--project", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "[alpha]" in out
    assert "[beta]" not in out


def test_migrate_rejects_unknown_project(chorus_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unconfigured project name exits 2 with a message."""
    from chorus.migrations.cli import main

    assert main(["apply", "--project", "ghost"]) == 2
    assert "unknown project" in capsys.readouterr().err
