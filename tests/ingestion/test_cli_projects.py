"""Ingestion CLI project-targeting tests (ADR 0017)."""

from __future__ import annotations

from pathlib import Path

import pytest
from neo4j import Driver

from tests.ingestion._fakes import FakeAdapter


def test_run_defaults_to_sole_project_and_uses_project_paths(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
    chorus_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``run`` without ``--project`` targets the sole project's state tree."""
    from chorus.ingestion import cli

    seen: dict[str, object] = {}

    def fake_adapter(src: Path) -> FakeAdapter:
        seen["source_dir"] = src
        return FakeAdapter()

    monkeypatch.setattr(cli, "FileUpstreamAdapter", fake_adapter)

    assert cli.main(["run"]) == 0
    assert seen["source_dir"] == chorus_env / "projects" / "default" / "ingest"
    assert (chorus_env / "projects" / "default" / "raw.sqlite").exists()
    assert "postings: 1" in capsys.readouterr().out


def test_run_requires_project_when_multiple_configured(
    chorus_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    neo4j_container: tuple[str, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With several projects configured, omitting ``--project`` exits 2."""
    uri, _user, _password = neo4j_container
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.setenv("NEO4J_URI_ALPHA", uri)
    monkeypatch.setenv("NEO4J_URI_BETA", uri)

    from chorus.ingestion import cli

    assert cli.main(["run"]) == 2
    assert "pass --project" in capsys.readouterr().err


def test_run_rejects_unknown_project(
    chorus_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unconfigured ``--project`` exits 2 with a message."""
    from chorus.ingestion import cli

    assert cli.main(["run", "--project", "ghost"]) == 2
    assert "unknown project" in capsys.readouterr().err
