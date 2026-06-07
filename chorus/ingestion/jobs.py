"""In-memory background-job registry for UI-triggered ingestion (ADR 0014).

The frontend ingestion path must not block the HTTP request: an ingest (with
inline NER) or a resolve pass can run for minutes, well past the UI client and
reverse-proxy timeouts. Endpoints stage their work, hand a callable to
:meth:`JobRegistry.submit`, and return ``202`` with a job id; the UI polls a
status endpoint until the job is terminal.

Design choices (see the ADR):

- **Ephemeral.** Job state lives only in process memory. A restart loses it,
  which is acceptable because the *results* are durable in Neo4j and the §76
  audit log — the registry only carries progress/outcome for polling.
- **Single worker.** A ``ThreadPoolExecutor(max_workers=1)`` serializes heavy
  Neo4j writers so two never overlap.
- **One active job.** :meth:`submit` rejects a second active job with
  :class:`JobBusyError` (checked atomically under the lock), so the UI can
  surface a clean "busy" state rather than silently queueing.

The module is pure stdlib (plus loguru) so it unit-tests without any service.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from loguru import logger

JobKind = Literal["ingest", "resolve"]
JobStatus = Literal["queued", "running", "done", "error"]

# Statuses that count as "in flight" for the single-active-job guard and for
# eviction (an active job is never evicted).
_ACTIVE: tuple[JobStatus, ...] = ("queued", "running")


class JobBusyError(RuntimeError):
    """Raised by :meth:`JobRegistry.submit` when a job is already active."""


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (ms precision)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass
class Job:
    """One unit of background work and its observable state.

    Attributes:
        id: Stable identifier (``"job-<n>"``) the UI polls on.
        kind: ``"ingest"`` or ``"resolve"``.
        status: Lifecycle state — ``queued`` → ``running`` → ``done``/``error``.
        result: The job function's return value when ``status == "done"``.
        error: Type-prefixed exception message when ``status == "error"``.
        created_by: Authenticated principal that submitted the job.
        created_at: ISO-8601 submission time.
        finished_at: ISO-8601 completion time; ``None`` until terminal.
    """

    id: str
    kind: JobKind
    status: JobStatus = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_by: str = ""
    created_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None


class JobRegistry:
    """Thread-safe registry running at most one background job at a time."""

    def __init__(self, max_jobs: int = 50) -> None:
        """Create an empty registry with a single-worker executor.

        Args:
            max_jobs: Soft cap on retained jobs; once exceeded, the oldest
                *finished* job is evicted on each submit. Active jobs are
                never evicted, so the live count may briefly exceed the cap
                when every retained job is still running.
        """
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._counter = 0
        self._max_jobs = max_jobs
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chorus-job")

    def submit(
        self,
        kind: JobKind,
        fn: Callable[[Job], dict[str, Any]],
        *,
        created_by: str = "",
    ) -> Job:
        """Register a job and schedule ``fn`` on the worker thread.

        Args:
            kind: Job kind, recorded on the :class:`Job`.
            fn: Callable invoked as ``fn(job)`` on the worker; its returned
                dict becomes ``job.result``. Exceptions are captured, not
                propagated (the job ends ``error``).
            created_by: Authenticated principal, recorded on the job.

        Returns:
            The freshly-created :class:`Job` (status ``queued``).

        Raises:
            JobBusyError: If a job is already active. The id counter is not
                advanced in this case.
        """
        with self._lock:
            if self._active_count_locked() >= 1:
                raise JobBusyError("another ingestion job is already running")
            self._counter += 1
            job = Job(id=f"job-{self._counter}", kind=kind, created_by=created_by)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._evict_locked()
        self._executor.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[Job], dict[str, Any]]) -> None:
        """Worker entry point: run ``fn`` and record the outcome on ``job``."""
        with self._lock:
            job.status = "running"
        try:
            result = fn(job)
        # Broad by design: the worker must never die on a job's failure — any
        # exception is recorded on the job and the worker stays available.
        except Exception as exc:
            with self._lock:
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = _now_iso()
            logger.warning("job {} ({}) failed: {}", job.id, job.kind, job.error)
            return
        with self._lock:
            job.status = "done"
            job.result = result
            job.finished_at = _now_iso()

    def get(self, job_id: str) -> Job | None:
        """Return the job with ``job_id``, or ``None`` if unknown/evicted."""
        with self._lock:
            return self._jobs.get(job_id)

    def active_count(self) -> int:
        """Return the number of queued or running jobs (0 or 1 in practice)."""
        with self._lock:
            return self._active_count_locked()

    def _active_count_locked(self) -> int:
        """Count active jobs; caller must hold ``self._lock``."""
        return sum(1 for j in self._jobs.values() if j.status in _ACTIVE)

    def _evict_locked(self) -> None:
        """Drop oldest finished jobs while over capacity; hold ``self._lock``.

        Active jobs are skipped, so a registry full of running jobs is left
        over capacity rather than dropping in-flight work. In practice the
        single-active-job guard bounds active jobs to one, so the only job
        ever preserved past the cap is the one currently running.
        """
        while len(self._order) > self._max_jobs:
            victim = next(
                (jid for jid in self._order if (j := self._jobs.get(jid)) is not None and j.status not in _ACTIVE),
                None,
            )
            if victim is None:
                break
            self._order.remove(victim)
            self._jobs.pop(victim, None)

    def shutdown(self) -> None:
        """Stop the worker, cancelling any not-yet-started job. Idempotent."""
        self._executor.shutdown(wait=False, cancel_futures=True)
