"""The backend's scheduled retention sweep.

While ``RETENTION_SWEEP_ENABLED`` is on and ``RETENTION_SWEEP_INTERVAL_S`` is
above zero, the backend sweeps on a background thread, as the system
(``Actor(None, "system")``): first shortly after startup, then once per
interval. A sweep that stops because an audit event could not be written
leaves the scheduler halted, visible as ``chorus_retention_sweep_halted`` and
in the operational log; the next interval tries again, and since a batch
commits only after its event is written, nothing is deleted while the trail
stays unwritable. At shutdown a running sweep is asked to stop after its
current batch, and waited for.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Final

from loguru import logger

from chorus.audit.trail import AuditFailure
from chorus.retention import metrics
from chorus.retention.sweeper import SweepBusy, SweepTotals

#: Seconds between startup and the first scheduled sweep (or the interval,
#: when that is shorter): startup's own work goes first.
FIRST_DELAY_S: Final = 60.0

#: One sweep: told when to stop, filling the totals as it goes.
SweepRun = Callable[[Callable[[], bool], SweepTotals], object]


class RetentionScheduler:
    """Runs a sweep on a daemon thread, once per interval, until stopped.

    Attributes:
        halted: Whether the last sweep stopped on an audit event that could
            not be written.
        runs: Sweeps attempted so far.
    """

    def __init__(self, run: SweepRun, *, interval_s: float, first_delay_s: float | None = None) -> None:
        """Prepare the schedule; nothing runs until :meth:`start`.

        Args:
            run: One sweep.
            interval_s: Seconds between sweeps; above zero.
            first_delay_s: Seconds before the first sweep; :data:`FIRST_DELAY_S`
                when ``None``, capped at the interval.

        Raises:
            ValueError: If ``interval_s`` is not above zero.
        """
        if interval_s <= 0:
            raise ValueError("a retention schedule needs an interval above zero")
        self._run = run
        self._interval_s = interval_s
        self._first_delay_s = min(FIRST_DELAY_S if first_delay_s is None else first_delay_s, interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.halted = False
        self.runs = 0

    def start(self) -> None:
        """Start the schedule's thread."""
        metrics.SCHEDULED.set(1)
        self._thread = threading.Thread(target=self._loop, name="retention-sweep", daemon=True)
        self._thread.start()
        logger.info("retention sweep scheduled every {:g} s", self._interval_s)

    def stop(self, timeout_s: float = 60.0) -> None:
        """Stop the schedule, waiting for a running sweep to end its batch.

        Args:
            timeout_s: How long to wait for the thread.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                logger.warning("retention sweep still running at shutdown; its open batch rolls back")
        metrics.SCHEDULED.set(0)

    def _loop(self) -> None:
        """Sleep, sweep, repeat, until stopped."""
        delay = self._first_delay_s
        while not self._stop.wait(delay):
            self.run_once()
            delay = self._interval_s

    def run_once(self) -> None:
        """Run one sweep now and record its outcome in the metrics.

        Never raises: a failed sweep is retried at the next interval.
        """
        self.runs += 1
        totals = SweepTotals()
        outcome = "ok"
        try:
            self._run(self._stop.is_set, totals)
        except AuditFailure:
            outcome = "halted"
        except SweepBusy:
            outcome = "busy"
        except Exception as exc:  # retried at the next interval; never ends the schedule
            outcome = "error"
            logger.error("scheduled retention sweep failed ({})", type(exc).__name__)
        finally:
            for kind, value in totals.as_dict().items():
                if kind not in {"batches", "stopped"} and value:
                    metrics.DELETED.labels(kind).inc(int(value))
        metrics.RUNS.labels(outcome).inc()
        if outcome == "ok":
            self.halted = False
            metrics.HALTED.set(0)
            metrics.LAST_SUCCESS.set(time.time())
        elif outcome == "halted":
            self.halted = True
            metrics.HALTED.set(1)
            logger.error("scheduled retention sweep halted; next attempt in {:g} s", self._interval_s)
        elif outcome == "busy":
            logger.info("scheduled retention sweep skipped: another sweep is running")
