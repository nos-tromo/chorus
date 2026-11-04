"""Retention CLI: ``python -m chorus.retention.cli {report,sweep}``.

``report`` prints the dry run and writes no audit event; ``sweep`` writes its
own chain to stdout (``AUDIT_OUTPUT`` unset) with the operator as the actor,
refuses while the sweeper is switched off, and follows
``AUDIT_REQUIRE_PURPOSE``. All values are invented.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from neo4j import Driver

from chorus.retention import cli
from chorus.retention.sweeper import sweep_lock
from tests._audit import assert_chain_ok, operation_events
from tests.retention._graph import posting, uuids

#: Ingested long enough ago that the 30-day period set below has run out.
_OLD = datetime.now(UTC) - timedelta(days=40)


@pytest.fixture(autouse=True)
def _cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A neutral CLI audit env, a 30-day period and the sweeper switched on.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    for key in ("AUDIT_REQUIRE_PURPOSE", "AUDIT_CASE_REF", "AUDIT_REASON", "AUDIT_OUTPUT"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("AUDIT_CLI_USER", "operator-1")
    monkeypatch.setenv("RETENTION_DAYS_DEFAULT", "30")
    monkeypatch.setenv("RETENTION_SWEEP_ENABLED", "true")
    monkeypatch.setenv("RETENTION_SWEEP_INTERVAL_S", "")
    monkeypatch.setenv("RETENTION_SWEEP_BATCH_SIZE", "")


def _chain(out: str) -> io.StringIO:
    """Pick the audit lines out of the CLI's stdout."""
    return io.StringIO("\n".join(line for line in out.splitlines() if '"stream":"audit"' in line) + "\n")


def test_report_prints_the_dry_run_and_records_nothing(
    migrated_driver: Driver, chorus_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``report`` counts the expired posting, prints no audit line and deletes nothing."""
    posting(migrated_driver, "p-old", author="author-alice", at=_OLD)

    assert cli.main(["report"]) == 0

    out = capsys.readouterr().out
    assert "Retention dry run" in out
    assert "posts: 1 (postings 1, comments 0, messages 0)" in out
    assert '"stream":"audit"' not in out
    assert "p-old" not in out
    assert uuids(migrated_driver) == {"p-old"}


def test_report_json_carries_the_numbers(
    migrated_driver: Driver, chorus_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``report --json`` prints the same report as JSON."""
    posting(migrated_driver, "p-old", author="author-alice", at=_OLD)

    assert cli.main(["report", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["delete"]["posts"]["total"] == 1
    assert report["config"] == {
        "sweep_enabled": True,
        "interval_s": 86400.0,
        "batch_size": 500,
        "retention_enabled": True,
        "period_days": 30,
    }


def test_sweep_deletes_and_records_as_the_operator(
    migrated_driver: Driver, chorus_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``sweep`` deletes the expired posting in a chain of its own, attributed to the operator."""
    posting(migrated_driver, "p-old", author="author-alice", at=_OLD)
    posting(migrated_driver, "p-new", author="author-alice", at=datetime.now(UTC))

    assert cli.main(["sweep"]) == 0

    out = capsys.readouterr().out
    assert "posts: 1" in out
    assert uuids(migrated_driver) == {"p-new"}
    chain = _chain(out)
    [event] = operation_events(chain)
    assert (event["action"], event["operation"], event["outcome"]) == ("delete", "retention.sweep", "ok")
    assert event["actor"] == {"user": "operator-1", "via": "cli"}
    assert event["target"]["ids"] == ["author:author-alice", "post:p-old"]
    assert_chain_ok(chain)


def test_sweep_refuses_while_switched_off(
    migrated_driver: Driver,
    chorus_env: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the switch off nothing is deleted; the refusal is recorded and the exit code is 2."""
    monkeypatch.setenv("RETENTION_SWEEP_ENABLED", "false")
    posting(migrated_driver, "p-old", author="author-alice", at=_OLD)

    assert cli.main(["sweep"]) == 2

    captured = capsys.readouterr()
    assert "switched off" in captured.err
    assert uuids(migrated_driver) == {"p-old"}
    chain = _chain(captured.out)
    [denied] = operation_events(chain)
    assert (denied["action"], denied["operation"], denied["outcome"]) == ("delete", "retention.sweep", "denied")
    assert denied["criteria"]["sweep_enabled"] is False
    assert "target" not in denied
    assert_chain_ok(chain)


def test_sweep_follows_the_purpose_policy(
    migrated_driver: Driver,
    chorus_env: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``delete`` needs a stated purpose, a sweep without one is refused; with one it runs."""
    monkeypatch.setenv("AUDIT_REQUIRE_PURPOSE", "delete")
    posting(migrated_driver, "p-old", author="author-alice", at=_OLD)

    assert cli.main(["sweep"]) == 2
    refused = _chain(capsys.readouterr().out)
    assert [e["outcome"] for e in operation_events(refused)] == ["denied"]
    assert uuids(migrated_driver) == {"p-old"}

    monkeypatch.setenv("AUDIT_REASON", "retention review")
    assert cli.main(["sweep"]) == 0
    chain = _chain(capsys.readouterr().out)
    [event] = operation_events(chain)
    assert event["purpose"] == {"reason": "retention review"}
    assert uuids(migrated_driver) == set()
    assert_chain_ok(refused)
    assert_chain_ok(chain)


def test_an_invalid_configuration_exits_2_before_any_event(
    chorus_env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch size below 1 is refused before the chain starts."""
    monkeypatch.setenv("RETENTION_SWEEP_BATCH_SIZE", "0")

    assert cli.main(["sweep"]) == 2

    captured = capsys.readouterr()
    assert "RETENTION_SWEEP_BATCH_SIZE" in captured.err
    assert '"stream":"audit"' not in captured.out


def test_sweep_exits_1_while_another_runs(
    migrated_driver: Driver, chorus_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sweep that finds the lock held deletes nothing and exits 1."""
    posting(migrated_driver, "p-old", author="author-alice", at=_OLD)

    with sweep_lock(chorus_env):
        assert cli.main(["sweep"]) == 1

    captured = capsys.readouterr()
    assert "another retention sweep is running" in captured.err
    assert uuids(migrated_driver) == {"p-old"}
    chain = _chain(captured.out)
    assert operation_events(chain) == []
    assert_chain_ok(chain)
