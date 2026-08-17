"""Thin CLI: ``python -m chorus.ingestion.cli run [--since ISO8601]``.

Mirrors the structure of :mod:`chorus.migrations.cli`: open the driver,
delegate to a runner, close the driver in ``finally``. The CLI itself
does no business logic; everything load-bearing lives in
:func:`chorus.ingestion.orchestrator.run_once`.

Per ADR 0017 every subcommand targets exactly one project's instance and
state tree (``--project``; defaults to the sole configured project). The
source directory is the project's ingest drop point, overridable via
``INGESTION_SOURCE_DIR`` in single-project compat mode only.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from chorus.audit.logger import AuditLogger
from chorus.db.registry import close_all_drivers, get_driver
from chorus.ingestion.orchestrator import run_once
from chorus.ingestion.raw_store import RawStore
from chorus.ingestion.resolution import backfill_norm_keys, resolve_all
from chorus.ingestion.upstream import FileUpstreamAdapter
from chorus.utils.env_cfg import (
    load_project_paths_env,
    load_projects_env,
    load_resolution_env,
    load_retention_env,
)


def _resolve_target_project(flag: str | None) -> str | None:
    """Pick the project a subcommand targets.

    Args:
        flag: The ``--project`` value, or ``None`` when omitted.

    Returns:
        The validated project name, or ``None`` after printing an error
        (unknown project, or ambiguous default with several configured).
    """
    configured = load_projects_env().names
    if flag is None:
        if len(configured) == 1:
            return configured[0]
        print(
            f"multiple projects configured ({', '.join(configured)}); pass --project NAME",
            file=sys.stderr,
        )
        return None
    if flag not in configured:
        print(f"unknown project: {flag}; configured: {', '.join(configured)}", file=sys.stderr)
        return None
    return flag


def main(argv: list[str] | None = None) -> int:
    """Run the ingestion CLI.

    Subcommands:
        - ``run``: pull every implemented upstream table once,
          persist the rows to the raw store, project them into the
          graph, and print per-stage counts. ``--since ISO8601``
          restricts the pull to rows newer than the cutoff.
        - ``resolve``: resolve unresolved aliases to canonical entities
          and print the per-method summary. ``--user`` sets the §76
          audit principal.
        - ``backfill-norm-keys``: stamp ``:Alias.norm_key`` on resolved
          aliases that predate the durable-key change (idempotent).

    Every subcommand accepts ``--project NAME``; with exactly one project
    configured it may be omitted.

    Args:
        argv: Argument vector to parse. ``None`` (the default) reads
            from ``sys.argv``.

    Returns:
        Process exit code (``0`` on success, ``2`` on unknown command,
        unknown project, or ambiguous project selection).
    """
    p = argparse.ArgumentParser(prog="chorus-ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one ingestion pass")
    run_p.add_argument(
        "--since",
        help="ISO 8601 timestamp; restrict the pull to rows newer than this",
        default=None,
    )
    resolve_p = sub.add_parser("resolve", help="resolve unresolved aliases to entities")
    resolve_p.add_argument(
        "--user",
        default=None,
        help="principal recorded in the §76 audit log (default: CHORUS_DEFAULT_IDENTITY or 'cli')",
    )
    backfill_p = sub.add_parser(
        "backfill-norm-keys",
        help="stamp :Alias.norm_key on resolved aliases that predate the durable-key change",
    )
    for cmd in (run_p, resolve_p, backfill_p):
        cmd.add_argument(
            "--project",
            default=None,
            metavar="NAME",
            help="project to target (default: the sole configured project)",
        )
    args = p.parse_args(argv)

    project = _resolve_target_project(args.project)
    if project is None:
        return 2
    paths = load_project_paths_env(project)

    if args.cmd == "run":
        since = datetime.fromisoformat(args.since) if args.since else None
        retention = load_retention_env()

        adapter = FileUpstreamAdapter(paths.ingest_source)
        raw = RawStore(paths.raw_store)
        raw.init_schema()

        try:
            result = run_once(adapter, get_driver(project), raw, retention, since=since)
        finally:
            close_all_drivers()

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

    if args.cmd == "resolve":
        principal = args.user or os.environ.get("CHORUS_DEFAULT_IDENTITY") or "cli"
        audit = AuditLogger(paths.audit_db)
        audit.init_schema()
        try:
            summary = resolve_all(get_driver(project), load_resolution_env(), audit, user=principal, project=project)
        finally:
            close_all_drivers()
        for field, count in summary.as_dict().items():
            print(f"{field}: {count}")
        return 0

    if args.cmd == "backfill-norm-keys":
        try:
            stamped = backfill_norm_keys(get_driver(project), load_resolution_env())
        finally:
            close_all_drivers()
        print(f"backfilled: {stamped}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
