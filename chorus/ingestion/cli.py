"""Thin CLI: ``python -m chorus.ingestion.cli run [--since ISO8601]``.

Mirrors the structure of :mod:`chorus.migrations.cli`: open the driver,
delegate to a runner, close the driver in ``finally``. The CLI itself
does no business logic; everything load-bearing lives in
:func:`chorus.ingestion.orchestrator.run_once`.

The source directory is configured via ``INGESTION_SOURCE_DIR`` and is
not overridable on the command line — matching the migrations CLI's
env-only target convention.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from chorus.db.neo4j import close_driver, get_driver
from chorus.ingestion.orchestrator import run_once
from chorus.ingestion.raw_store import RawStore
from chorus.ingestion.upstream import FileUpstreamAdapter
from chorus.utils.env_cfg import (
    load_ingestion_env,
    load_path_env,
    load_retention_env,
)


def main(argv: list[str] | None = None) -> int:
    """Run the ingestion CLI.

    Subcommands:
        - ``run``: pull every implemented upstream table once,
          persist the rows to the raw store, project them into the
          graph, and print per-stage counts. ``--since ISO8601``
          restricts the pull to rows newer than the cutoff.

    Args:
        argv: Argument vector to parse. ``None`` (the default) reads
            from ``sys.argv``.

    Returns:
        Process exit code (``0`` on success, ``2`` on unknown command).
    """
    p = argparse.ArgumentParser(prog="chorus-ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one ingestion pass")
    run_p.add_argument(
        "--since",
        help="ISO 8601 timestamp; restrict the pull to rows newer than this",
        default=None,
    )
    args = p.parse_args(argv)

    if args.cmd == "run":
        since = datetime.fromisoformat(args.since) if args.since else None
        ingest_cfg = load_ingestion_env()
        retention = load_retention_env()
        paths = load_path_env()

        adapter = FileUpstreamAdapter(ingest_cfg.source_dir)
        raw = RawStore(paths.raw_store)
        raw.init_schema()

        driver = get_driver()
        try:
            result = run_once(adapter, driver, raw, retention, since=since)
        finally:
            close_driver()

        for stage, count in result["counts"].items():
            print(f"{stage}: {count}")
        if result["skipped"]:
            print(f"skipped: {result['skipped']}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
