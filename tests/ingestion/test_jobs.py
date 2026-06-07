"""Unit tests for the in-memory background-job registry.

Pure stdlib — no Neo4j, no FastAPI. The worker runs on a real thread, so
tests coordinate with ``threading.Event`` and poll for terminal state with
a timeout rather than asserting immediately after ``submit``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from chorus.ingestion.jobs import Job, JobBusyError, JobRegistry


def _wait_terminal(reg: JobRegistry, job_id: str, timeout: float = 5.0) -> Job:
    """Poll ``reg`` until ``job_id`` reaches a terminal state or time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = reg.get(job_id)
        if job is not None and job.status in ("done", "error"):
            return job
        time.sleep(0.01)
    job = reg.get(job_id)
    raise AssertionError(f"job {job_id} not terminal in {timeout}s; status={job and job.status}")


def _wait_running(reg: JobRegistry, started: threading.Event, timeout: float = 5.0) -> None:
    """Block until the worker has entered the job function (status running)."""
    if not started.wait(timeout=timeout):
        raise AssertionError("job did not start within timeout")


def _boom(_job: Job) -> dict[str, Any]:
    """A job fn that always raises (typed to satisfy the submit signature)."""
    raise ValueError("boom")


def _blocking_fn(
    started: threading.Event, release: threading.Event, result: dict[str, Any] | None = None
) -> Callable[[Job], dict[str, Any]]:
    """Build a job fn that signals ``started`` then waits for ``release``."""

    def _fn(_job: Job) -> dict[str, Any]:
        started.set()
        if not release.wait(timeout=5.0):
            raise TimeoutError("release event never signaled")
        return result if result is not None else {"ok": True}

    return _fn


def test_job_runs_to_done_and_records_result() -> None:
    """A job that returns a dict ends ``done`` with that dict as ``result``."""
    reg = JobRegistry()
    try:
        job = reg.submit("ingest", lambda _job: {"value": 42}, created_by="alice")
        assert job.id == "job-1"
        assert job.created_by == "alice"
        terminal = _wait_terminal(reg, job.id)
        assert terminal.status == "done"
        assert terminal.result == {"value": 42}
        assert terminal.error is None
        assert terminal.finished_at is not None
    finally:
        reg.shutdown()


def test_job_failure_is_captured_not_raised() -> None:
    """A raising job ends ``error`` with a type-prefixed message, no result."""
    reg = JobRegistry()
    try:
        job = reg.submit("ingest", _boom)
        terminal = _wait_terminal(reg, job.id)
        assert terminal.status == "error"
        assert terminal.error == "ValueError: boom"
        assert terminal.result is None
        assert terminal.finished_at is not None
    finally:
        reg.shutdown()


def test_worker_survives_a_failed_job() -> None:
    """After a job errors, the single worker keeps serving later jobs."""
    reg = JobRegistry()
    try:
        bad = reg.submit("ingest", _boom)
        _wait_terminal(reg, bad.id)
        good = reg.submit("resolve", lambda _job: {"ok": True})
        terminal = _wait_terminal(reg, good.id)
        assert terminal.status == "done"
        assert terminal.result == {"ok": True}
    finally:
        reg.shutdown()


def test_second_submit_while_active_raises_busy() -> None:
    """Only one job may be active; a concurrent submit is rejected."""
    reg = JobRegistry()
    started, release = threading.Event(), threading.Event()
    try:
        first = reg.submit("ingest", _blocking_fn(started, release))
        _wait_running(reg, started)
        assert reg.active_count() == 1
        with pytest.raises(JobBusyError):
            reg.submit("resolve", lambda _job: {"ok": True})
    finally:
        release.set()
        _wait_terminal(reg, first.id)
        reg.shutdown()
    assert reg.active_count() == 0


def test_rejected_submit_does_not_consume_an_id() -> None:
    """A busy-rejected submit must not advance the id counter."""
    reg = JobRegistry()
    started, release = threading.Event(), threading.Event()
    try:
        first = reg.submit("ingest", _blocking_fn(started, release))
        _wait_running(reg, started)
        assert first.id == "job-1"
        with pytest.raises(JobBusyError):
            reg.submit("resolve", lambda _job: {"ok": True})
        release.set()
        _wait_terminal(reg, first.id)
        second = reg.submit("resolve", lambda _job: {"ok": True})
        assert second.id == "job-2"
    finally:
        reg.shutdown()


def test_ids_are_sequential() -> None:
    """Successful submits mint ``job-1``, ``job-2``, … in order."""
    reg = JobRegistry()
    try:
        a = reg.submit("ingest", lambda _job: {"ok": True})
        _wait_terminal(reg, a.id)
        b = reg.submit("ingest", lambda _job: {"ok": True})
        _wait_terminal(reg, b.id)
        assert (a.id, b.id) == ("job-1", "job-2")
    finally:
        reg.shutdown()


def test_eviction_drops_oldest_finished_over_capacity() -> None:
    """With ``max_jobs`` exceeded, the oldest finished job is evicted."""
    reg = JobRegistry(max_jobs=2)
    try:
        ids = []
        for _ in range(3):
            j = reg.submit("ingest", lambda _job: {"ok": True})
            _wait_terminal(reg, j.id)
            ids.append(j.id)
        assert ids == ["job-1", "job-2", "job-3"]
        assert reg.get("job-1") is None
        assert reg.get("job-2") is not None
        assert reg.get("job-3") is not None
    finally:
        reg.shutdown()


def test_eviction_never_drops_an_active_job() -> None:
    """Eviction skips the running job even when it is the oldest over capacity."""
    reg = JobRegistry(max_jobs=1)
    started, release = threading.Event(), threading.Event()
    try:
        first = reg.submit("ingest", lambda _job: {"ok": True})
        _wait_terminal(reg, first.id)
        second = reg.submit("ingest", _blocking_fn(started, release))
        _wait_running(reg, started)
        # Over capacity (2 > 1): the finished job-1 is evicted, the running
        # job-2 is preserved.
        assert reg.get("job-1") is None
        running = reg.get("job-2")
        assert running is not None and running.status == "running"
    finally:
        release.set()
        _wait_terminal(reg, second.id)
        reg.shutdown()


def test_shutdown_is_idempotent() -> None:
    """Calling ``shutdown`` twice does not raise."""
    reg = JobRegistry()
    reg.shutdown()
    reg.shutdown()
