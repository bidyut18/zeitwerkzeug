"""Async execution engine for drifting schedules."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from datetime import date, datetime, timedelta
from heapq import heappop

from zeitwerkzeug.daemon.clock import SystemClock, _ensure_utc
from zeitwerkzeug.daemon.constants import _EMPTY_QUEUE_SLEEP_SECONDS, _MIDNIGHT_LOOP_SLEEP_MINUTES
from zeitwerkzeug.daemon.execution import ExecutionMixin
from zeitwerkzeug.daemon.models import ExecutionRecord, QueueEntry
from zeitwerkzeug.daemon.registry import FuzzyCron
from zeitwerkzeug.daemon.scheduling import SchedulingMixin
from zeitwerkzeug.daemon.sleeping import SleepingMixin


class ExecutionLoop(SleepingMixin, SchedulingMixin, ExecutionMixin):
    """Async scheduler designed for moving targets."""

    def __init__(
        self,
        registry: FuzzyCron | None = None,
        *,
        clock: SystemClock | None = None,
        midnight_recalibration: bool = True,
        max_concurrency: int = 32,
        default_job_timeout: timedelta | None = timedelta(minutes=5),
        default_condition_timeout: timedelta | None = timedelta(seconds=30),
        default_max_latency: timedelta | None = None,
        retry_jitter_seconds: float = 0.0,
        history_limit: int = 1000,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1.")

        if history_limit < 0:
            raise ValueError("history_limit must be zero or greater.")

        self.registry = registry if registry is not None else FuzzyCron.default()
        self.clock = clock if clock is not None else SystemClock()
        self.midnight_recalibration = midnight_recalibration

        self.max_concurrency = max_concurrency
        self.default_job_timeout = default_job_timeout
        self.default_condition_timeout = default_condition_timeout
        self.default_max_latency = default_max_latency
        self.retry_jitter_seconds = retry_jitter_seconds

        self._queue: list[QueueEntry] = []
        self._generations: dict[uuid.UUID, int] = {}
        self._last_refresh: dict[uuid.UUID, date] = {}
        self._active: set[uuid.UUID] = set()

        self._running = False
        self._wake = asyncio.Event()
        self._until: datetime | None = None

        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._available = max_concurrency
        self._inflight: set[asyncio.Task[None]] = set()
        self._history: deque[ExecutionRecord] = deque(maxlen=history_limit)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._running = False
        self._wake.set()

    @property
    def history(self) -> tuple[ExecutionRecord, ...]:
        """Return execution history, newest last."""
        return tuple(self._history)

    @property
    def history_limit(self) -> int:
        """Maximum number of execution records kept in memory."""
        return self._history.maxlen or 0

    @property
    def available_concurrency(self) -> int:
        """Number of concurrency slots currently free."""
        return self._available

    @property
    def queue_snapshot(self) -> tuple[QueueEntry, ...]:
        """Return a point-in-time copy of the internal queue."""
        return tuple(self._queue)

    def peek_next(self) -> QueueEntry | None:
        """Return the earliest valid queue entry, discarding stale ones."""
        return self._peek_valid()

    def refresh_job(self, job_id: uuid.UUID) -> None:
        """Bump *job_id*'s generation and schedule its next occurrence."""
        registry = self.registry
        job = registry.get_job(job_id)
        if job is None:
            return
        self._refresh_job(job, self.clock.now())

    def sync_registry(self) -> None:
        """Activate new jobs and remove deleted ones from the queue."""
        self._populate_missing()

    async def acquire_concurrency_slot(self) -> bool:
        """Acquire a concurrency slot directly."""
        semaphore = self._semaphore
        await semaphore.acquire()
        self._available -= 1
        return True

    def release_concurrency_slot(self) -> None:
        """Release a previously acquired concurrency slot safely."""
        max_concurrency = self.max_concurrency
        if self._available < max_concurrency:
            self._semaphore.release()
            self._available += 1

    def stats(self) -> dict[str, int | bool]:
        """Return basic operational metrics."""
        running = self._running
        queue_depth = len(self._queue)
        active_jobs = len(self._active)
        inflight_jobs = len(self._inflight)
        history_size = len(self._history)
        max_concurrency = self.max_concurrency
        available_concurrency = self._available

        return {
            "running": running,
            "queue_depth": queue_depth,
            "active_jobs": active_jobs,
            "inflight_jobs": inflight_jobs,
            "history_size": history_size,
            "max_concurrency": max_concurrency,
            "available_concurrency": available_concurrency,
        }

    async def run(self, *, until: datetime | None = None) -> None:
        """Run the daemon until stopped or until an optional deadline."""
        self._running = True
        self._until = _ensure_utc(until) if until is not None else None

        self._populate_all(self.clock.now())

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._queue_loop())

                if self.midnight_recalibration:
                    tg.create_task(self._midnight_loop())
        finally:
            inflight = self._inflight
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)

            self._running = False

    # ------------------------------------------------------------------
    # Main loops
    # ------------------------------------------------------------------

    async def _queue_loop(self) -> None:
        """Continuously execute jobs as their scheduled times arrive."""
        _peek_valid = self._peek_valid
        _populate_missing = self._populate_missing
        _acquire_concurrency_slot = self._acquire_concurrency_slot
        release_concurrency_slot = self.release_concurrency_slot
        _sleep_with_wake = self._sleep_with_wake
        _wait_until = self._wait_until
        _execute_entry = self._execute_entry
        _queue = self._queue
        _inflight = self._inflight
        _inflight_add = _inflight.add
        _inflight_discard = _inflight.discard
        create_task = asyncio.create_task
        empty_queue_sleep = timedelta(seconds=_EMPTY_QUEUE_SLEEP_SECONDS)

        while self._running:
            until = self._until
            if until is not None and self.clock.now() >= until:
                self.stop()
                break

            _populate_missing()
            entry = _peek_valid()

            if entry is None:
                await _sleep_with_wake(empty_queue_sleep)
                continue

            now = self.clock.now()
            when = entry.when

            if when > now:
                await _wait_until(when)
                continue

            acquired = await _acquire_concurrency_slot()

            if not acquired:
                continue

            entry = _peek_valid()
            now = self.clock.now()

            if entry is None or entry.when > now:
                release_concurrency_slot()
                continue

            heappop(_queue)

            task = create_task(_execute_entry(entry))
            _inflight_add(task)
            task.add_done_callback(_inflight_discard)

    async def _midnight_loop(self) -> None:
        """Recalibrate job schedules at local midnight for each timezone."""
        _sleep_with_wake = self._sleep_with_wake
        _next_refresh_time = self._next_refresh_time
        _refresh_due_jobs = self._refresh_due_jobs
        _wait_until = self._wait_until
        midnight_sleep = timedelta(minutes=_MIDNIGHT_LOOP_SLEEP_MINUTES)

        while self._running:
            now = self.clock.now()
            next_refresh = _next_refresh_time(now)

            if next_refresh is None:
                await _sleep_with_wake(midnight_sleep)
                continue

            if next_refresh > now:
                await _wait_until(next_refresh)

            if not self._running:
                break

            _refresh_due_jobs(self.clock.now())
