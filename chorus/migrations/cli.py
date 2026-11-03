"""Thin CLI: `python -m chorus.migrations.cli apply` (or `status`).

``apply`` runs in an audit chain of its own (``chorus.audit.cli``): each
applied migration writes one ``migration.apply`` event attributed to the CLI
operator. ``status`` only lists versions and is not audited.
"""

from __future__ import annotations

import argparse
import sys

from chorus.audit.cli import run_audited_command
from chorus.audit.trail import AuditContext
from chorus.db.neo4j import close_driver, get_driver
from chorus.migrations.runner import (
    MIGRATION_OPERATION,
    applied_versions,
    apply_all,
    migration_actions,
    pending_migrations,
)


def _apply(audit: AuditContext) -> int:
    """Apply pending migrations, refusing first when a purpose is required.

    Args:
        audit: The CLI operator's audit context.

    Returns:
        ``0``.
    """
    driver = get_driver()
    try:
        pending = pending_migrations(driver)
        if pending:
            audit.require_purpose(
                migration_actions(pending),
                MIGRATION_OPERATION,
                {"versions": [m.version for m in pending]},
            )
        newly = apply_all(driver, audit=audit)
    finally:
        close_driver()
    if newly:
        for v in newly:
            print(f"applied {v}")
    else:
        print("up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the migrations CLI.

    Subcommands:
        - ``apply``: apply any pending migrations and print each applied
          version, or ``"up to date"`` when nothing was pending.
        - ``status``: print the sorted list of applied migration versions.

    Args:
        argv: Argument vector to parse. ``None`` (the default) reads
            from ``sys.argv``.

    Returns:
        Process exit code: ``0`` on success, ``2`` on an unknown command or
        a required purpose that was not stated, ``1`` when the audit trail
        cannot be written.
    """
    p = argparse.ArgumentParser(prog="chorus-migrate")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply", help="apply any pending migrations")
    sub.add_parser("status", help="show applied migration versions")
    args = p.parse_args(argv)

    if args.cmd == "apply":
        return run_audited_command(_apply)
    if args.cmd == "status":
        driver = get_driver()
        try:
            for v in sorted(applied_versions(driver)):
                print(v)
        finally:
            close_driver()
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
