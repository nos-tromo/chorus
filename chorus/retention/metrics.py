"""Prometheus metrics of the backend's scheduled retention sweep.

Aggregate counts only, no ids, on the default registry that ``GET /metrics``
exposes (``METRICS_ENABLED``). A sweep stopped by an audit event that could
not be written shows as ``chorus_retention_sweep_halted 1`` until a later
sweep writes one again.

This module defines its metrics once per process; it reads no environment
and must not be re-imported (a second definition would clash in the
registry).
"""

from __future__ import annotations

from typing import Final

from prometheus_client import Counter, Gauge

SCHEDULED: Final = Gauge(
    "chorus_retention_sweep_scheduled",
    "1 while the backend sweeps expired posts on a schedule, else 0.",
)
HALTED: Final = Gauge(
    "chorus_retention_sweep_halted",
    "1 after a scheduled sweep stopped because an audit event could not be written, until a later one is; else 0.",
)
LAST_SUCCESS: Final = Gauge(
    "chorus_retention_sweep_last_success_timestamp_seconds",
    "Unix time at which the last scheduled sweep finished without error.",
)
RUNS: Final = Counter(
    "chorus_retention_sweep_runs",
    "Scheduled sweeps by outcome: ok, halted, error, busy.",
    ["outcome"],
)
DELETED: Final = Counter(
    "chorus_retention_deleted",
    "What scheduled sweeps deleted, by kind: posts, aliases, hashtags, attachments, raw_rows, upload_files.",
    ["kind"],
)
