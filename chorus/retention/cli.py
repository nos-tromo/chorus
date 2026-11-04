"""Retention CLI: ``python -m chorus.retention.cli {report,sweep}``.

``report`` is the dry run: what a sweep would delete now and what it would
leave, as counts and date ranges (``--json`` for the raw numbers). It
records nothing, deletes nothing, and works while the sweeper is off.

``sweep`` runs one sweep now, in an audit chain of its own: the operator is
``AUDIT_CLI_USER``, the purpose ``AUDIT_CASE_REF`` / ``AUDIT_REASON``
(``chorus.audit.cli``). It deletes only while ``RETENTION_SWEEP_ENABLED`` is
on; otherwise the refusal is recorded as ``denied`` and it exits 2. It exits
1 when another sweep is running or an audit event cannot be written.

Both read their settings from the environment, like the backend; the make
targets run them inside the running backend (``docker compose exec``).
"""

from __future__ import annotations

import argparse
import json
import sys

from chorus.audit.cli import run_audited_command
from chorus.audit.trail import AuditContext
from chorus.db.neo4j import close_driver, get_driver
from chorus.ingestion.raw_store import RawStore
from chorus.retention.report import build_report, render_report
from chorus.retention.sweeper import (
    SWEEP_ACTIONS,
    SWEEP_OPERATION,
    SweepBusy,
    SweepTotals,
    describe_config,
    sweep,
)
from chorus.utils.env_cfg import RetentionSweepConfig, load_path_env, load_retention_env, load_retention_sweep_env


def _report(as_json: bool) -> int:
    """Print what a sweep would delete now.

    Args:
        as_json: Print the report's numbers as JSON instead of text.

    Returns:
        ``0``, or ``2`` when the sweeper configuration is invalid.
    """
    try:
        config = load_retention_sweep_env()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    paths = load_path_env()
    driver = get_driver()
    try:
        report = build_report(driver, paths.raw_store, paths.chorus_home, retention=load_retention_env(), config=config)
    finally:
        close_driver()
    print(json.dumps(report, indent=2) if as_json else render_report(report))
    return 0


def _sweep(audit: AuditContext, config: RetentionSweepConfig) -> int:
    """Run one sweep and print what it deleted.

    Args:
        audit: The CLI operator's audit context.
        config: The sweeper configuration.

    Returns:
        ``0`` on success, ``2`` while sweeping is switched off, ``1`` when
        another sweep is running.
    """
    retention = load_retention_env()
    criteria = dict(describe_config(retention, config))
    if not config.enabled:
        audit.deny("delete", SWEEP_OPERATION, criteria)
        print(
            "error: retention sweeping is switched off (RETENTION_SWEEP_ENABLED); nothing was deleted",
            file=sys.stderr,
        )
        return 2
    audit.require_purpose(SWEEP_ACTIONS, SWEEP_OPERATION, criteria)
    paths = load_path_env()
    totals = SweepTotals()
    driver = get_driver()
    try:
        sweep(
            driver,
            RawStore(paths.raw_store),
            paths.chorus_home,
            audit=audit,
            retention=retention,
            config=config,
            totals=totals,
        )
    except SweepBusy:
        print("error: another retention sweep is running; nothing was deleted", file=sys.stderr)
        return 1
    finally:
        close_driver()
    for name, value in totals.as_dict().items():
        print(f"{name}: {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the retention CLI.

    Subcommands:
        - ``report [--json]``: the dry run; records and deletes nothing.
        - ``sweep``: one sweep now, recorded on the audit trail.

    Args:
        argv: Argument vector to parse. ``None`` (the default) reads from
            ``sys.argv``.

    Returns:
        Process exit code: ``0`` on success; ``2`` for an unknown command, an
        invalid sweeper configuration, sweeping switched off or a required
        purpose not stated; ``1`` when another sweep is running or the audit
        trail cannot be written.
    """
    p = argparse.ArgumentParser(prog="chorus-retention")
    sub = p.add_subparsers(dest="cmd", required=True)
    report_p = sub.add_parser("report", help="dry run: count what a sweep would delete now")
    report_p.add_argument("--json", action="store_true", help="print the numbers as JSON")
    sub.add_parser("sweep", help="delete the expired posts now (needs RETENTION_SWEEP_ENABLED)")
    args = p.parse_args(argv)

    if args.cmd == "report":
        return _report(bool(args.json))
    if args.cmd == "sweep":
        try:
            config = load_retention_sweep_env()
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return run_audited_command(lambda audit: _sweep(audit, config))
    return 2


if __name__ == "__main__":
    sys.exit(main())
