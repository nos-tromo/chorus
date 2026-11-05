"""The backend's scheduled retention sweep: outcomes, metrics, shutdown, lifespan.

The unit tests drive :class:`RetentionScheduler` with a fake sweep; the last
test runs the real backend lifespan against the Neo4j testcontainer and
waits for its scheduled sweep. All values are invented.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from neo4j import Driver
from prometheus_client import REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator

from chorus.audit.trail import AuditFailure, AuditTrail
from chorus.retention import scheduler as scheduler_module
from chorus.retention.scheduler import RetentionScheduler
from chorus.retention.sweeper import SweepBusy, SweepTotals
from chorus.utils.audit_event import AuditEmitter
from tests._audit import assert_chain_ok, audit_events, operation_events
from tests.retention._graph import posting, uuids


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    """Read a metric from the default registry.

    Args:
        name: The sample's name.
        labels: Its labels.

    Returns:
        The value, ``0.0`` when it was never set.
    """
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_a_halted_sweep_shows_until_a_later_one_records() -> None:
    """A failed audit write halts the schedule (gauge 1); the next sweep that records clears it."""
    outcomes: list[type[Exception] | None] = [AuditFailure, None]

    def _run(_should_stop: Callable[[], bool], totals: SweepTotals) -> None:
        totals.posts += 2
        error = outcomes.pop(0)
        if error is not None:
            raise error("synthetic")

    halted_runs = _sample("chorus_retention_sweep_runs_total", {"outcome": "halted"})
    ok_runs = _sample("chorus_retention_sweep_runs_total", {"outcome": "ok"})
    posts = _sample("chorus_retention_deleted_total", {"kind": "posts"})
    schedule = RetentionScheduler(_run, interval_s=3600)

    schedule.run_once()
    assert schedule.halted
    assert _sample("chorus_retention_sweep_halted") == 1
    schedule.run_once()
    assert not schedule.halted
    assert _sample("chorus_retention_sweep_halted") == 0

    assert _sample("chorus_retention_sweep_runs_total", {"outcome": "halted"}) == halted_runs + 1
    assert _sample("chorus_retention_sweep_runs_total", {"outcome": "ok"}) == ok_runs + 1
    assert _sample("chorus_retention_deleted_total", {"kind": "posts"}) == posts + 4
    assert _sample("chorus_retention_sweep_last_success_timestamp_seconds") > 0


@pytest.mark.parametrize(("error", "outcome"), [(SweepBusy, "busy"), (RuntimeError, "error")])
def test_a_failed_sweep_never_ends_the_schedule(error: type[Exception], outcome: str) -> None:
    """A busy lock or any other failure is counted and logged by type, and the schedule goes on."""
    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), level="INFO")

    def _run(_should_stop: Callable[[], bool], _totals: SweepTotals) -> None:
        raise error("synthetic detail with author-alice")

    before = _sample("chorus_retention_sweep_runs_total", {"outcome": outcome})
    try:
        RetentionScheduler(_run, interval_s=3600).run_once()
    finally:
        logger.remove(sink)

    assert _sample("chorus_retention_sweep_runs_total", {"outcome": outcome}) == before + 1
    assert not any("author-alice" in line for line in logs)


def test_the_schedule_repeats_and_stops_between_batches() -> None:
    """Sweeps repeat every interval; stopping asks the running one to end, and waits."""
    started = threading.Event()
    seen_stop: list[bool] = []

    def _run(should_stop: Callable[[], bool], _totals: SweepTotals) -> None:
        started.set()
        deadline = time.monotonic() + 5
        while not should_stop() and time.monotonic() < deadline:
            time.sleep(0.01)
        seen_stop.append(should_stop())

    schedule = RetentionScheduler(_run, interval_s=0.05, first_delay_s=0)
    schedule.start()
    assert started.wait(5)
    assert _sample("chorus_retention_sweep_scheduled") == 1
    schedule.stop(timeout_s=5)

    assert seen_stop == [True]
    assert schedule.runs == 1
    assert _sample("chorus_retention_sweep_scheduled") == 0


def test_no_schedule_without_an_interval() -> None:
    """An interval of zero means one-off sweeps only: the backend schedules none."""
    with pytest.raises(ValueError, match="interval"):
        RetentionScheduler(lambda _s, _t: None, interval_s=0)


def test_the_metrics_endpoint_carries_the_sweep_gauges() -> None:
    """GET /metrics exposes the halted gauge next to the request metrics."""
    app = FastAPI()
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    text = TestClient(app).get("/metrics").text

    assert "chorus_retention_sweep_halted" in text
    assert "chorus_retention_deleted_total" in text


def test_the_backend_sweeps_on_its_schedule(
    migrated_driver: Driver, chorus_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switched on with an interval, the running backend deletes expired posts as the system."""
    posting(migrated_driver, "p-old", author="author-alice", at=datetime.now(UTC) - timedelta(days=40))
    posting(migrated_driver, "p-new", author="author-alice", at=datetime.now(UTC))
    for key, value in {
        "RETENTION_SWEEP_ENABLED": "true",
        "RETENTION_SWEEP_INTERVAL_S": "3600",
        "RETENTION_DAYS_DEFAULT": "30",
        "AUDIT_REQUIRE_PURPOSE": "",
        "AUDIT_OUTPUT": "",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(scheduler_module, "FIRST_DELAY_S", 0.0)
    stream = io.StringIO()

    def _open(
        cls: type[AuditTrail],
        output: str | None,
        *,
        required: frozenset[str] = frozenset(),
        heartbeat_interval_s: float = 60.0,
    ) -> AuditTrail:
        return cls(AuditEmitter("chorus", "test", stream=stream, heartbeat_interval_s=0), required=required)

    monkeypatch.setattr(AuditTrail, "open", classmethod(_open))
    from chorus.api import main

    remaining: set[str] = set()

    async def _serve() -> None:
        async with main.lifespan(main.app):
            assert isinstance(main.app.state.retention, RetentionScheduler)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and "p-old" in uuids(migrated_driver):
                await asyncio.sleep(0.05)
            remaining.update(uuids(migrated_driver))  # shutdown closes the shared driver

    asyncio.run(_serve())

    assert remaining == {"p-new"}
    events = audit_events(stream)
    assert events[1]["criteria"]["retention_sweep"] == "scheduled"
    [deleted] = [e for e in operation_events(stream) if e["action"] == "delete"]
    assert (deleted["operation"], deleted["actor"]) == ("retention.sweep", {"user": None, "via": "system"})
    assert events[-1]["operation"] == "shutdown"
    assert_chain_ok(stream)
