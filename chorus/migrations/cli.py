"""Thin CLI: `python -m chorus.migrations.cli apply`."""

from __future__ import annotations

import argparse
import sys

from chorus.db.neo4j import close_driver, get_driver
from chorus.migrations.runner import apply_all, applied_versions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chorus-migrate")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply", help="apply any pending migrations")
    sub.add_parser("status", help="show applied migration versions")
    args = p.parse_args(argv)

    driver = get_driver()
    try:
        if args.cmd == "apply":
            newly = apply_all(driver)
            if newly:
                for v in newly:
                    print(f"applied {v}")
            else:
                print("up to date")
            return 0
        if args.cmd == "status":
            for v in sorted(applied_versions(driver)):
                print(v)
            return 0
        return 2
    finally:
        close_driver()


if __name__ == "__main__":
    sys.exit(main())
