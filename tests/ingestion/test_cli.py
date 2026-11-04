"""Ingestion CLI: ``python -m chorus.ingestion.cli {run,resolve,backfill-norm-keys}``.

The CLI is a thin shell around :func:`chorus.ingestion.orchestrator.run_audited`
and the resolution stage. Tests verify the argparse surface, the ``--since``
parse path, the printed per-stage counts, and the audit chain each command
writes to stdout (``AUDIT_OUTPUT`` unset), with the exit codes for a missing
purpose. All values are invented.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, override

import pytest
from neo4j import Driver

from chorus.audit.trail import AuditContext
from chorus.ingestion.adapter import UpstreamAdapter
from chorus.ingestion.raw_store import RawStore
from chorus.utils.env_cfg import RetentionConfig
from tests._audit import assert_chain_ok, operation_events
from tests.ingestion._fakes import FakeAdapter


@pytest.fixture(autouse=True)
def _cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A neutral CLI audit env: no purpose policy, no stated purpose, a known operator.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    for key in ("AUDIT_REQUIRE_PURPOSE", "AUDIT_CASE_REF", "AUDIT_REASON", "AUDIT_OUTPUT"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("AUDIT_CLI_USER", "operator-1")


def _chain(out: str) -> io.StringIO:
    """Pick the audit lines out of the CLI's stdout."""
    return io.StringIO("\n".join(line for line in out.splitlines() if '"stream":"audit"' in line) + "\n")


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

    chain = _chain(out)
    collect = operation_events(chain)[0]
    assert (collect["action"], collect["operation"], collect["outcome"]) == ("collect", "ingest.run", "ok")
    assert collect["actor"] == {"user": "operator-1", "via": "cli"}
    assert collect["target"]["kind"] == "author"
    assert collect["target"]["count"] == len(collect["target"]["ids"]) > 0
    assert collect["criteria"]["graph"]["postings"] == 1
    assert collect["criteria"]["raw_store"]["connections"] == 1
    assert_chain_ok(chain)


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

    def fake_run_audited(
        adapter: UpstreamAdapter,
        driver: Driver,
        raw: RawStore,
        retention: RetentionConfig,
        *,
        audit: AuditContext,
        operation: str,
        criteria: Mapping[str, object],
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Capture call args without touching the database."""
        seen["since"] = since
        seen["criteria"] = dict(criteria)
        return {"counts": {}, "skipped": [], "dropped": {}, "filtered": {}}

    class _Adapter(FakeAdapter):
        @override
        def source_files(self) -> list[str]:
            return ["2026-05_postings.csv"]

    monkeypatch.setattr(cli, "FileUpstreamAdapter", lambda _src: _Adapter())
    monkeypatch.setattr(cli, "run_audited", fake_run_audited)

    exit_code = cli.main(["run", "--since", "2026-05-01T00:00:00+00:00"])
    assert exit_code == 0
    assert seen["since"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert seen["criteria"] == {"since": "2026-05-01T00:00:00+00:00", "files": ["2026-05_postings.csv"]}


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


def test_resolve_records_the_run_with_the_operator(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``resolve`` runs resolution, prints the summary, and records it as the CLI operator."""
    from chorus.inference import provider
    from chorus.ingestion import cli

    with migrated_driver.session() as s:
        s.run("MERGE (a:Alias {surface_form: 'Trier'}) ON CREATE SET a.label = 'LOCATION'")
    # one non-zero vector per text (zero-norm vectors are rejected by the index)
    monkeypatch.setattr(provider, "embed", lambda texts, **kw: [[1.0] + [0.0] * 1023 for _ in texts])
    monkeypatch.setenv("AUDIT_CASE_REF", "case-0001")

    exit_code = cli.main(["resolve"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "processed: 1" in out
    assert "minted: 1" in out

    chain = _chain(out)
    combine, embed = operation_events(chain)
    assert (combine["action"], combine["operation"]) == ("combine", "resolution.run")
    assert combine["actor"] == {"user": "operator-1", "via": "cli"}
    assert combine["purpose"] == {"case_ref": "case-0001"}
    assert combine["target"]["count"] == 1
    assert embed["operation"] == "resolution.embed"
    assert_chain_ok(chain)


@pytest.mark.parametrize(
    ("argv", "required", "action", "operation"),
    [
        (["run"], "collect", "collect", "ingest.run"),
        (["resolve"], "combine", "combine", "resolution.run"),
        (["backfill-norm-keys"], "modify", "modify", "resolution.backfill_norm_keys"),
    ],
)
def test_missing_purpose_exits_2_with_one_denied_event(
    migrated_driver: Driver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    required: str,
    action: str,
    operation: str,
) -> None:
    """With the command's action required and no purpose stated, nothing runs: exit 2, one denied event."""
    from chorus.ingestion import cli

    monkeypatch.setattr(cli, "FileUpstreamAdapter", lambda _src: FakeAdapter())
    monkeypatch.setenv("AUDIT_REQUIRE_PURPOSE", required)
    assert cli.main(argv) == 2

    captured = capsys.readouterr()
    assert captured.err.strip() == (
        f"error: {operation} needs a stated purpose: set AUDIT_CASE_REF and/or AUDIT_REASON"
    )
    chain = _chain(captured.out)
    [event] = operation_events(chain)
    assert (event["action"], event["operation"], event["outcome"]) == (action, operation, "denied")
    assert event["actor"] == {"user": "operator-1", "via": "cli"}
    assert_chain_ok(chain)
    with migrated_driver.session() as s:
        record = s.run("MATCH (n) WHERE NOT n:_Migration RETURN count(n) AS n").single()
    assert record is not None and record["n"] == 0  # nothing was written


def test_cli_audit_failure_exits_1(
    migrated_driver: Driver, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A collection whose event cannot be written fails the command with exit code 1."""
    from chorus.audit.trail import AuditFailure
    from chorus.ingestion import cli

    def _fails(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise AuditFailure("an audit event could not be written")

    monkeypatch.setattr(cli, "FileUpstreamAdapter", lambda _src: FakeAdapter())
    monkeypatch.setattr(cli, "run_audited", _fails)
    assert cli.main(["run"]) == 1
    assert "audit event could not be written" in capsys.readouterr().err


def test_backfill_norm_keys_cli(
    migrated_driver: Driver,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``backfill-norm-keys`` stamps norm_key on resolved aliases that lack it.

    Aliases resolved before the durable-key change carry no norm_key. The
    backfill must compute it with the same Python ``normalize_surface``
    (``str.casefold()``) used at resolution time — not Cypher ``toLower()``,
    which differs for non-ASCII (German ``ß`` → ``ss`` under casefold but
    unchanged under toLower). Unresolved aliases are left untouched, and the
    command is idempotent.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
        capsys: stdout capture so we can assert on the printed count.
    """
    from chorus.ingestion import cli

    with migrated_driver.session() as s:
        s.run(
            """
            CREATE (e1:Entity {id:'e-strasse', canonical_name:'Straße', type:'LOCATION'})
            CREATE (a1:Alias {surface_form:'Straße', label:'LOCATION'})
            CREATE (a1)-[:RESOLVED_TO]->(e1)
            CREATE (e2:Entity {id:'e-berlin', canonical_name:'Berlin', type:'LOCATION'})
            CREATE (a2:Alias {surface_form:'  Berlin ', label:'LOCATION'})
            CREATE (a2)-[:RESOLVED_TO]->(e2)
            CREATE (a3:Alias {surface_form:'Unresolved', label:'LOCATION'})
            """
        )

    exit_code = cli.main(["backfill-norm-keys"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "backfilled: 2" in out
    [event] = operation_events(_chain(out))
    assert (event["action"], event["operation"]) == ("modify", "resolution.backfill_norm_keys")
    assert event["target"] == {"kind": "alias", "count": 2}

    with migrated_driver.session() as s:
        rows = {r["sf"]: r["nk"] for r in s.run("MATCH (a:Alias) RETURN a.surface_form AS sf, a.norm_key AS nk")}
    assert rows["Straße"] == "strasse"  # casefold (ß→ss), NOT Cypher toLower ('straße')
    assert rows["  Berlin "] == "berlin"  # trimmed + casefold
    assert rows["Unresolved"] is None  # only resolved aliases are backfilled

    # Idempotent: a second run finds nothing left to stamp, and records nothing.
    exit_code = cli.main(["backfill-norm-keys"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "backfilled: 0" in out
    assert operation_events(_chain(out)) == []
