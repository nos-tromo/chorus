"""Migrations apply idempotently and create the expected schema."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from neo4j import Driver

from chorus.audit.trail import AuditContext, AuditTrail
from chorus.utils.audit_event import Actor
from tests._audit import assert_chain_ok, operation_events


def test_apply_is_idempotent(migrated_driver: Driver, audit_ctx: AuditContext, audit_stream: io.StringIO) -> None:
    """Re-applying migrations on an up-to-date database is a no-op, and records nothing.

    Args:
        migrated_driver: Driver against an already-migrated database.
        audit_ctx: The test's audit context.
        audit_stream: The captured audit stream.
    """
    from chorus.migrations.runner import apply_all

    second = apply_all(migrated_driver, audit=audit_ctx)
    assert second == []
    assert operation_events(audit_stream) == []


def test_schema_migrations_are_admin_events(driver: Driver, audit_trail: AuditTrail, audit_stream: io.StringIO) -> None:
    """Each applied schema migration writes one admin event with its version and counters.

    Args:
        driver: Driver against a clean, un-migrated database.
        audit_trail: The test's audit trail.
        audit_stream: The captured audit stream.
    """
    from chorus.migrations.runner import _discover, apply_all

    newly = apply_all(driver, audit=audit_trail.system_context())
    assert newly == [version for version, _ in _discover()]

    events = operation_events(audit_stream)
    assert [e["target"]["ids"] for e in events] == [[v] for v in newly]
    for event in events:
        assert (event["action"], event["operation"], event["outcome"]) == ("admin", "migration.apply", "ok")
        assert event["actor"] == {"user": None, "via": "system"}
        assert event["target"]["kind"] == "migration"
        assert event["target"]["count"] == 1
        assert event["criteria"]["statements"] >= 1
    constraints = events[0]["criteria"]["changes"]
    assert constraints == {"constraints_added": events[0]["criteria"]["statements"]}


def _write_migration(directory: Path, name: str, body: str) -> None:
    """Write one migration file into a temporary migrations directory."""
    (directory / name).write_text(body, encoding="utf-8")


def test_data_migration_is_a_modify_event_with_counters(
    migrated_driver: Driver,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A migration with a data statement is a modify event carrying what it changed.

    Args:
        migrated_driver: Driver against an already-migrated database.
        audit_trail: The test's audit trail.
        audit_stream: The captured audit stream.
        tmp_path: A temporary migrations directory.
        monkeypatch: pytest monkeypatch fixture.
    """
    from chorus.migrations import runner

    with migrated_driver.session() as s:
        s.run("CREATE (:Alias {surface_form: 'Example One'}), (:Alias {surface_form: 'Example Two'})").consume()
    _write_migration(
        tmp_path,
        "900_example_backfill.cypher",
        "CREATE INDEX example_index IF NOT EXISTS FOR (a:Alias) ON (a.example);\n"
        "MATCH (a:Alias) WHERE a.example IS NULL SET a.example = true;\n",
    )
    monkeypatch.setattr(runner, "MIGRATION_DIR", tmp_path)

    newly = runner.apply_all(migrated_driver, audit=audit_trail.context(Actor(user="operator-1", via="cli")))
    assert newly == ["900_example_backfill"]

    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("modify", "migration.apply", "ok")
    assert event["actor"] == {"user": "operator-1", "via": "cli"}
    assert event["target"] == {"kind": "migration", "ids": ["900_example_backfill"], "count": 1}
    assert event["criteria"] == {"statements": 2, "changes": {"indexes_added": 1, "properties_set": 2}}


def test_failed_migration_writes_an_error_event(
    migrated_driver: Driver,
    audit_trail: AuditTrail,
    audit_stream: io.StringIO,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A migration whose statement fails is recorded as an error and stops the run.

    Args:
        migrated_driver: Driver against an already-migrated database.
        audit_trail: The test's audit trail.
        audit_stream: The captured audit stream.
        tmp_path: A temporary migrations directory.
        monkeypatch: pytest monkeypatch fixture.
    """
    from neo4j.exceptions import CypherSyntaxError

    from chorus.migrations import runner

    _write_migration(tmp_path, "901_broken.cypher", "THIS IS NOT CYPHER;\n")
    monkeypatch.setattr(runner, "MIGRATION_DIR", tmp_path)

    with pytest.raises(CypherSyntaxError):
        runner.apply_all(migrated_driver, audit=audit_trail.system_context())
    [event] = operation_events(audit_stream)
    assert (event["action"], event["operation"], event["outcome"]) == ("modify", "migration.apply", "error")
    assert event["target"]["ids"] == ["901_broken"]
    assert runner.pending_versions(migrated_driver) == ["901_broken"]


def test_cli_apply_needs_a_purpose_when_required(
    driver: Driver, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrations.cli apply refuses with exit code 2 and one denied event; nothing is applied.

    Args:
        driver: Driver against a clean, un-migrated database.
        monkeypatch: pytest monkeypatch fixture.
        capsys: Captures the CLI's stdout, where its audit chain goes.
    """
    from chorus.migrations import cli, runner

    monkeypatch.setenv("AUDIT_REQUIRE_PURPOSE", "admin")
    monkeypatch.setenv("AUDIT_CLI_USER", "operator-1")
    monkeypatch.setenv("AUDIT_CASE_REF", "")
    monkeypatch.setenv("AUDIT_REASON", "")
    assert cli.main(["apply"]) == 2

    captured = capsys.readouterr()
    assert "needs a stated purpose" in captured.err
    out = io.StringIO(captured.out)
    [event] = operation_events(out)
    assert (event["action"], event["operation"], event["outcome"]) == ("admin", "migration.apply", "denied")
    assert event["actor"] == {"user": "operator-1", "via": "cli"}
    assert_chain_ok(out)
    from chorus.db.neo4j import get_driver

    assert runner.applied_versions(get_driver()) == set()  # the CLI closed the shared driver


def test_cli_apply_records_each_migration_with_the_operator(
    driver: Driver, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With a stated purpose, the CLI chain records every applied migration.

    Args:
        driver: Driver against a clean, un-migrated database.
        monkeypatch: pytest monkeypatch fixture.
        capsys: Captures the CLI's stdout, where its audit chain goes.
    """
    from chorus.migrations import cli

    monkeypatch.setenv("AUDIT_REQUIRE_PURPOSE", "admin")
    monkeypatch.setenv("AUDIT_CLI_USER", "operator-1")
    monkeypatch.setenv("AUDIT_CASE_REF", "case-0001")
    monkeypatch.setenv("AUDIT_REASON", "")
    assert cli.main(["apply"]) == 0

    out = io.StringIO("\n".join(line for line in capsys.readouterr().out.splitlines() if '"stream":"audit"' in line))
    events = operation_events(out)
    assert events and all(e["operation"] == "migration.apply" for e in events)
    assert all(e["purpose"] == {"case_ref": "case-0001"} for e in events)
    assert_chain_ok(out)


def test_malformed_cli_purpose_exits_2_without_a_chain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A purpose with a control character is refused before anything is written.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        capsys: Captures the CLI's output.
    """
    from chorus.migrations import cli

    monkeypatch.setenv("AUDIT_REASON", "line\nbreak")
    assert cli.main(["apply"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "line" not in captured.err.replace("purpose field", "")


def test_cli_exits_1_when_the_audit_trail_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown AUDIT_OUTPUT mode stops the command before it touches the store.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        capsys: Captures the CLI's output.
    """
    from chorus.migrations import cli

    monkeypatch.setenv("AUDIT_OUTPUT", "syslog")
    assert cli.main(["apply"]) == 1
    assert "audit trail cannot be opened" in capsys.readouterr().err


def test_cli_status_is_not_audited(migrated_driver: Driver, capsys: pytest.CaptureFixture[str]) -> None:
    """Listing applied versions reads no personal data and writes no chain.

    Args:
        migrated_driver: Driver against an already-migrated database.
        capsys: Captures the CLI's stdout.
    """
    from chorus.migrations import cli

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert '"stream":"audit"' not in out
    assert "001_constraints" in out


def test_pending_versions_lists_all_discovered_on_fresh_db(driver: Driver) -> None:
    """On a fresh database every discovered migration is pending, in order.

    Args:
        driver: Driver against a clean, un-migrated database.
    """
    from chorus.migrations.runner import _discover, pending_versions

    expected = [version for version, _ in _discover()]
    assert expected, "expected at least one migration file to be discovered"
    assert pending_versions(driver) == expected


def test_pending_versions_empty_after_apply(migrated_driver: Driver) -> None:
    """Once every migration is applied, nothing is pending.

    Args:
        migrated_driver: Driver against an already-migrated database.
    """
    from chorus.migrations.runner import pending_versions

    assert pending_versions(migrated_driver) == []


def test_constraints_present(migrated_driver: Driver) -> None:
    """Every uniqueness constraint the data model relies on exists.

    Acts as a regression guard against accidental migration ordering or
    constraint-name drift.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    expected = {
        "post_uuid",
        "author_id",
        "entity_id",
        "hashtag_tag",
        "platform_name",
        "group_id",
        "alias_surface",
        "migration_version",
    }
    with migrated_driver.session() as s:
        names = {r["name"] for r in s.run("SHOW CONSTRAINTS YIELD name")}
    missing = expected - names
    assert not missing, f"missing constraints: {missing}"


def test_vector_indexes_present(migrated_driver: Driver) -> None:
    """Post and Entity vector indexes are created and sized by EMBED_DIM.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    with migrated_driver.session() as s:
        rows = s.run("SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name").data()
    names = {r["name"] for r in rows}
    assert {"post_embedding", "entity_embedding"} <= names


def test_alias_norm_key_index_present(migrated_driver: Driver) -> None:
    """The :Alias.norm_key range index backs durable cross-run resolution.

    ``norm_key`` holds the normalized (trim + casefold) surface form the
    resolution stage writes when it links an alias. Many raw ``surface_form``
    values share one ``norm_key``, so this is a NON-UNIQUE range index, not a
    constraint. See issue #24 / ADR 0012.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    with migrated_driver.session() as s:
        rows = s.run("SHOW INDEXES YIELD name, type RETURN name, type").data()
    by_name = {r["name"]: r["type"] for r in rows}
    assert "alias_norm_key" in by_name, f"alias_norm_key index missing; have {sorted(by_name)}"
    assert by_name["alias_norm_key"] == "RANGE"


def test_relationship_indexes_present(migrated_driver: Driver) -> None:
    """Edge indexes for FOLLOWS / FRIENDS_WITH / MENTIONS are present.

    Connections ingestion is stubbed but the indexes exist already so
    the eventual bulk load is index-backed from day one.

    Args:
        migrated_driver: Driver against a freshly-migrated database.
    """
    with migrated_driver.session() as s:
        rows = s.run("SHOW INDEXES YIELD name, type RETURN name, type").data()
    names = {r["name"] for r in rows}
    assert {"follows_crawled", "friends_crawled", "mentions_model_version"} <= names
