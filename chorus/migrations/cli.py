"""Thin CLI: `python -m chorus.migrations.cli apply`.

Per ADR 0017 each configured project has its own Neo4j instance; both
subcommands iterate every configured project by default, and ``--project``
narrows the run.
"""

from __future__ import annotations

import argparse
import sys

from chorus.db.registry import close_all_drivers, get_driver
from chorus.migrations.runner import applied_versions, apply_all
from chorus.utils.env_cfg import load_projects_env


def main(argv: list[str] | None = None) -> int:
    """Run the migrations CLI.

    Subcommands:
        - ``apply``: apply any pending migrations and print each applied
          version, or ``"up to date"`` when nothing was pending.
        - ``status``: print the sorted list of applied migration versions.

    Both accept ``--project NAME`` (repeatable); the default is every
    configured project. Output lines are prefixed ``[<project>]``.

    Args:
        argv: Argument vector to parse. ``None`` (the default) reads
            from ``sys.argv``.

    Returns:
        Process exit code (``0`` on success, ``2`` on unknown command or
        unknown project).
    """
    p = argparse.ArgumentParser(prog="chorus-migrate")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("apply", "apply any pending migrations"),
        ("status", "show applied migration versions"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument(
            "--project",
            action="append",
            dest="projects",
            metavar="NAME",
            help="project to target (repeatable; default: all configured)",
        )
    args = p.parse_args(argv)

    configured = load_projects_env().names
    targets = list(args.projects or configured)
    unknown = [t for t in targets if t not in configured]
    if unknown:
        print(f"unknown project(s): {', '.join(unknown)}; configured: {', '.join(configured)}", file=sys.stderr)
        return 2

    try:
        if args.cmd == "apply":
            for project in targets:
                newly = apply_all(get_driver(project))
                if newly:
                    for v in newly:
                        print(f"[{project}] applied {v}")
                else:
                    print(f"[{project}] up to date")
            return 0
        if args.cmd == "status":
            for project in targets:
                for v in sorted(applied_versions(get_driver(project))):
                    print(f"[{project}] {v}")
            return 0
        return 2
    finally:
        close_all_drivers()


if __name__ == "__main__":
    sys.exit(main())
