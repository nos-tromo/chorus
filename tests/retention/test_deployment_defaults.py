"""What a deployment gets: the sweeper off, and its commands run inside the backend.

The production shape (``docker/compose.yaml``) must not delete anything
until an operator switches the sweeper on, and the make targets must run in
the running backend (``docker compose exec``), where a one-off sweep's audit
chain lands in the backend's log. Read from the files, so these tests follow
a change to them.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_compose_keeps_the_sweeper_off_by_default() -> None:
    """The backend's RETENTION_SWEEP_ENABLED defaults to false, empty included."""
    compose = (_ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8")

    assert re.search(r"^\s+RETENTION_SWEEP_ENABLED: \$\{RETENTION_SWEEP_ENABLED:-false\}$", compose, re.MULTILINE)


def test_the_retention_targets_run_inside_the_running_backend() -> None:
    """The dry run and the one-off sweep exec into the backend; the sweep as the operator."""
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")

    report = re.search(r"^retention-report:\n\t(.+)$", makefile, re.MULTILINE)
    sweep = re.search(r"^retention-sweep:\n\t(.+)$", makefile, re.MULTILINE)
    assert report is not None and sweep is not None
    assert report.group(1) == "$(COMPOSE) exec backend python -m chorus.retention.cli report"
    assert sweep.group(1) == "$(AUDIT_EXEC) python -m chorus.retention.cli sweep"
    assert "run --rm" not in makefile
