"""Ingestion CLI: ``python -m chorus.ingestion.cli run [--since ISO8601]``.

The CLI is a thin shell around :func:`chorus.ingestion.orchestrator.run_once`.
Tests verify the argparse surface, the ``--since`` parse path, and that
the printed output exposes the per-stage counts and the connections
skip so an operator can see what happened.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from neo4j import Driver

from chorus.ingestion.adapter import UpstreamAdapter
from chorus.ingestion.raw_store import RawStore
from chorus.utils.env_cfg import RetentionConfig
from tests.ingestion._fakes import FakeAdapter


def test_run_invokes_orchestrator_and_prints_counts(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``run`` returns 0 and prints the per-stage counts and skipped stages.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest fixture used to swap the file adapter for
            the in-memory fake.
        capsys: stdout capture so we can assert on the printed output.
    """
    from chorus.ingestion import cli

    monkeypatch.setattr(cli, "FileUpstreamAdapter", lambda _src: FakeAdapter())

    exit_code = cli.main(["run"])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "postings: 1" in out
    assert "comments: 1" in out
    assert "messages: 1" in out
    assert "profiles: 1" in out
    assert "connections: 1" in out


def test_run_parses_since_and_passes_to_orchestrator(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--since ISO8601`` is parsed and forwarded to ``run_once``.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        monkeypatch: pytest fixture used to capture the ``since`` value
            the orchestrator receives.
    """
    from chorus.ingestion import cli

    seen: dict[str, object] = {}

    def fake_run_once(
        adapter: UpstreamAdapter,
        driver: Driver,
        raw: RawStore,
        retention: RetentionConfig,
        *,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Capture call args without touching the database."""
        seen["since"] = since
        return {"counts": {}, "skipped": [], "dropped": {}, "filtered": {}}

    monkeypatch.setattr(cli, "FileUpstreamAdapter", lambda _src: FakeAdapter())
    monkeypatch.setattr(cli, "run_once", fake_run_once)

    exit_code = cli.main(["run", "--since", "2026-05-01T00:00:00+00:00"])
    assert exit_code == 0
    assert seen["since"] == datetime(2026, 5, 1, tzinfo=UTC)


def test_unknown_subcommand_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown subcommands cause argparse to exit non-zero.

    Mirrors the migrations CLI behavior. The CLI never silently
    no-ops on a typo.
    """
    from chorus.ingestion import cli

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus"])
    assert excinfo.value.code != 0


def test_resolve_writes_audit_row_with_principal(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``resolve --user X`` runs resolution, prints the summary, and audits as X."""
    import sqlite3

    from chorus.inference import provider
    from chorus.ingestion import cli

    with migrated_driver.session() as s:
        s.run("MERGE (a:Alias {surface_form: 'Trier'}) ON CREATE SET a.label = 'LOCATION'")
    # one non-zero vector per text (zero-norm vectors are rejected by the index)
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [[1.0] + [0.0] * 1023 for _ in texts])

    exit_code = cli.main(["resolve", "--user", "operator-1"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "processed: 1" in out
    assert "minted: 1" in out

    from chorus.utils.env_cfg import load_audit_env

    rows = sqlite3.connect(load_audit_env().db_path).execute("SELECT user, tool_name FROM audit_log").fetchall()
    assert ("operator-1", "resolve_all") in rows
