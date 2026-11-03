"""Thin CLI: ``python -m chorus.ingestion.cli {run,resolve,backfill-norm-keys}``.

Mirrors the structure of :mod:`chorus.migrations.cli`: open the driver,
delegate to a runner, close the driver in ``finally``. The CLI itself
does no business logic; everything load-bearing lives in
:mod:`chorus.ingestion.orchestrator` and :mod:`chorus.ingestion.resolution`.

Every subcommand runs in an audit chain of its own (``chorus.audit.cli``):
the operator is ``AUDIT_CLI_USER``, the stated purpose ``AUDIT_CASE_REF`` /
``AUDIT_REASON``, and a command whose actions need a purpose that was not
stated is refused with exit code 2 before it touches anything.

The source directory is configured via ``INGESTION_SOURCE_DIR`` and is
not overridable on the command line — matching the migrations CLI's
env-only target convention.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from chorus.audit.cli import run_audited_command
from chorus.audit.trail import AuditContext
from chorus.db.neo4j import close_driver, get_driver
from chorus.ingestion.orchestrator import ingest_actions, run_audited
from chorus.ingestion.raw_store import RawStore
from chorus.ingestion.resolution import (
    BACKFILL_OPERATION,
    RESOLUTION_ACTIONS,
    RESOLUTION_OPERATION,
    backfill_norm_keys,
    resolve_all,
)
from chorus.ingestion.upstream import FileUpstreamAdapter
from chorus.utils.env_cfg import (
    load_ingestion_env,
    load_path_env,
    load_resolution_env,
    load_retention_env,
)

#: The audit operation of a CLI ingestion pass.
INGEST_OPERATION = "ingest.run"


def _run(audit: AuditContext, since: datetime | None) -> int:
    """Run one ingestion pass from ``INGESTION_SOURCE_DIR`` and print its counts.

    Args:
        audit: The CLI operator's audit context.
        since: Only rows newer than this, or ``None`` for a full pull.

    Returns:
        ``0``.
    """
    adapter = FileUpstreamAdapter(load_ingestion_env().source_dir)
    criteria: dict[str, object] = {
        "since": since.isoformat() if since else None,
        "files": adapter.source_files(),
    }
    audit.require_purpose(ingest_actions(), INGEST_OPERATION, criteria)
    raw = RawStore(load_path_env().raw_store)
    raw.init_schema()

    driver = get_driver()
    try:
        result = run_audited(
            adapter,
            driver,
            raw,
            load_retention_env(),
            audit=audit,
            operation=INGEST_OPERATION,
            criteria=criteria,
            since=since,
        )
    finally:
        close_driver()

    for stage, count in result["counts"].items():
        print(f"{stage}: {count}")
    if result["skipped"]:
        print(f"skipped: {result['skipped']}")
    dropped = {stage: n for stage, n in result["dropped"].items() if n}
    if dropped:
        print(f"dropped (malformed): {dropped}")
    filtered = {stage: n for stage, n in result["filtered"].items() if n}
    if filtered:
        print(f"filtered (structural): {filtered}")
    return 0


def _resolve(audit: AuditContext) -> int:
    """Resolve unresolved aliases to entities and print the per-method summary.

    Args:
        audit: The CLI operator's audit context.

    Returns:
        ``0``.
    """
    audit.require_purpose(RESOLUTION_ACTIONS, RESOLUTION_OPERATION)
    driver = get_driver()
    try:
        summary = resolve_all(driver, load_resolution_env(), audit)
    finally:
        close_driver()
    for field, count in summary.as_dict().items():
        print(f"{field}: {count}")
    return 0


def _backfill(audit: AuditContext) -> int:
    """Stamp ``:Alias.norm_key`` on resolved aliases that lack it and print the count.

    Args:
        audit: The CLI operator's audit context.

    Returns:
        ``0``.
    """
    audit.require_purpose(("modify",), BACKFILL_OPERATION)
    driver = get_driver()
    try:
        stamped = backfill_norm_keys(driver, load_resolution_env(), audit=audit)
    finally:
        close_driver()
    print(f"backfilled: {stamped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the ingestion CLI.

    Subcommands:
        - ``run``: pull every implemented upstream table once,
          persist the rows to the raw store, project them into the
          graph, and print per-stage counts. ``--since ISO8601``
          restricts the pull to rows newer than the cutoff.
        - ``resolve``: resolve unresolved aliases to canonical entities
          and print the per-method summary.
        - ``backfill-norm-keys``: stamp ``:Alias.norm_key`` on resolved
          aliases that predate the durable-key change (idempotent).

    Args:
        argv: Argument vector to parse. ``None`` (the default) reads
            from ``sys.argv``.

    Returns:
        Process exit code: ``0`` on success, ``2`` on an unknown command or
        a required purpose that was not stated, ``1`` when the audit trail
        cannot be written.
    """
    p = argparse.ArgumentParser(prog="chorus-ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one ingestion pass")
    run_p.add_argument(
        "--since",
        help="ISO 8601 timestamp; restrict the pull to rows newer than this",
        default=None,
    )
    sub.add_parser("resolve", help="resolve unresolved aliases to entities")
    sub.add_parser(
        "backfill-norm-keys",
        help="stamp :Alias.norm_key on resolved aliases that predate the durable-key change",
    )
    args = p.parse_args(argv)

    if args.cmd == "run":
        since = datetime.fromisoformat(args.since) if args.since else None
        return run_audited_command(lambda audit: _run(audit, since))
    if args.cmd == "resolve":
        return run_audited_command(_resolve)
    if args.cmd == "backfill-norm-keys":
        return run_audited_command(_backfill)
    return 2


if __name__ == "__main__":
    sys.exit(main())
