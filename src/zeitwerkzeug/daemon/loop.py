"""Async execution engine for drifting schedules."""

from __future__ import annotations

import asyncio
import heapq
import inspect
import logging
import random
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple

from zeitwerkzeug.daemon.cron import FuzzyCron, JobSpec
from zeitwerkzeug.exceptions import ConditionEvaluationError, ZeitwerkzeugError
from zeitwerkzeug.interfaces import ConditionPlugin, ExecutionContext

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Timing constants
# --------------------------------------------------------------------

_MAX_WAIT_SECONDS = 300.0
_EMPTY_QUEUE_SLEEP_SECONDS = 30
_MIDNIGHT_LOOP_SLEEP_MINUTES = 5
_SEMAPHORE_POLL_SECONDS = 1.0


def _ensure_utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)

    return dt.astimezone(UTC)


class SystemClock:
    """Default wall-clock time source."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """A single historical execution result."""

    job_id: uuid.UUID
    job_name: str
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime
    attempt: int
    status: str
    error: str | None = None

    @property
    def duration(self) -> timedelta:
        """Total time spent evaluating and executing."""
        return self.finished_at - self.started_at


class QueueEntry(NamedTuple):
    """A single item in the execution heap.

    Fields are ordered so that heap comparison works correctly.
    """

    when: datetime
    generation: int
    job_id: uuid.UUID
    attempt: int


class ExecutionLoop:
    """Async scheduler designed for moving targets.

    Features:
        - resolves trigger times lazily
        - supports condition plugins
        - supports retry policies
        - recalculates jobs at local midnight per job timezone
        - safe for sync and async job functions
        - supports concurrent job execution
        - supports condition and job timeouts
        - supports missed-run skipping
        - keeps in-memory execution history
    """

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

        self.registry = registry or FuzzyCron.default()
        self.clock = clock or SystemClock()
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

    def stats(self) -> dict[str, int | bool]:
        """Return basic operational metrics."""
        return {
            "running": self._running,
            "queue_depth": len(self._queue),
            "active_jobs": len(self._active),
            "inflight_jobs": len(self._inflight),
            "history_size": len(self._history),
            "max_concurrency": self.max_concurrency,
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
            if self._inflight:
                await asyncio.gather(*self._inflight, return_exceptions=True)

            self._running = False

    # ------------------------------------------------------------------
    # Job registry management
    # ------------------------------------------------------------------

    def _populate_all(self, now: datetime) -> None:
        """Schedule every job that is not yet active."""
        for job in self.registry.jobs:
            if job.id not in self._active:
                self._refresh_job(job, now)

    def _populate_missing(self) -> None:
        """Remove deleted jobs and schedule newly added ones."""
        now = self.clock.now()
        current_ids = {job.id for job in self.registry.jobs}

        # Clean up jobs that have been removed from the registry.
        for job_id in list(self._active):
            if job_id not in current_ids:
                self._active.discard(job_id)
                self._generations.pop(job_id, None)
                self._last_refresh.pop(job_id, None)

        # Activate any new jobs.
        for job in self.registry.jobs:
            if job.id not in self._active:
                self._refresh_job(job, now)

    def _refresh_job(self, job: JobSpec, now: datetime) -> None:
        """Bump a job's generation and schedule its next occurrence."""
        generation = self._generations.get(job.id, 0) + 1

        self._generations[job.id] = generation
        self._active.add(job.id)
        self._last_refresh[job.id] = self._local_date(job, now)

        self._schedule_next(job, after=now, attempt=1, generation=generation)

    def _local_date(self, job: JobSpec, now: datetime) -> date:
        """Return the current date in a job's local timezone."""
        return now.astimezone(job.trigger.timezone_info).date()

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    def _schedule_next(
        self,
        job: JobSpec,
        after: datetime,
        attempt: int,
        generation: int,
    ) -> None:
        """Resolve the next trigger time for *job* and push it onto the heap."""
        try:
            next_at = job.trigger.resolve_after(after)
        except ZeitwerkzeugError as exc:
            logger.warning("Could not schedule job %s: %s", job.name, exc)
            return

        self._push(next_at, job.id, attempt, generation)

    def _schedule_retry(
        self,
        job: JobSpec,
        now: datetime,
        attempt: int,
    ) -> None:
        """Schedule a retry or fall back to the next regular occurrence."""
        policy = job.trigger.fail_policy
        generation = self._generations.get(job.id, 1)

        if policy is None:
            self._schedule_next(job, now, attempt=1, generation=generation)
            return

        if policy.max_attempts is not None and attempt >= policy.max_attempts:
            logger.info(
                "Job %s reached max attempts; scheduling next occurrence.",
                job.name,
            )
            self._schedule_next(job, now, attempt=1, generation=generation)
            return

        try:
            limit_utc = policy.resolve_limit(now, job.trigger)
        except ZeitwerkzeugError:
            limit_utc = None

        next_retry = now + policy.retry_interval

        if self.retry_jitter_seconds > 0:
            jitter = random.uniform(0.0, self.retry_jitter_seconds)
            next_retry += timedelta(seconds=jitter)

        if limit_utc is not None and next_retry > limit_utc:
            logger.info(
                "Job %s retry limit reached; scheduling next occurrence.",
                job.name,
            )
            self._schedule_next(job, now, attempt=1, generation=generation)
            return

        self._push(next_retry, job.id, attempt + 1, generation)

    def _push(
        self,
        when: datetime,
        job_id: uuid.UUID,
        attempt: int,
        generation: int,
    ) -> None:
        """Insert an entry into the priority queue and wake the loop."""
        heapq.heappush(
            self._queue,
            QueueEntry(_ensure_utc(when), generation, job_id, attempt),
        )
        self._wake.set()

    def _peek_valid(self) -> QueueEntry | None:
        """Return the earliest queue entry whose generation is still current.

        Stale entries are discarded.
        """
        while self._queue:
            entry = self._queue[0]
            job = self.registry.get_job(entry.job_id)
            current_generation = self._generations.get(entry.job_id)

            if job is None or current_generation != entry.generation:
                heapq.heappop(self._queue)
                continue

            return entry

        return None

    # ------------------------------------------------------------------
    # Main loops
    # ------------------------------------------------------------------

    async def _queue_loop(self) -> None:
        """Continuously execute jobs as their scheduled times arrive."""
        while self._running:
            if self._until is not None and self.clock.now() >= self._until:
                self.stop()
                break

            self._populate_missing()
            entry = self._peek_valid()

            if entry is None:
                await self._sleep_with_wake(timedelta(seconds=_EMPTY_QUEUE_SLEEP_SECONDS))
                continue

            now = self.clock.now()

            if entry.when > now:
                await self._wait_until(entry.when)
                continue

            acquired = await self._acquire_concurrency_slot()

            if not acquired:
                continue

            # Re-check after acquiring the slot because the queue may have
            # changed while waiting for concurrency capacity.
            entry = self._peek_valid()
            now = self.clock.now()

            if entry is None or entry.when > now:
                self._semaphore.release()
                continue

            heapq.heappop(self._queue)

            task = asyncio.create_task(self._execute_entry(entry))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _midnight_loop(self) -> None:
        """Recalibrate job schedules at local midnight for each timezone."""
        while self._running:
            now = self.clock.now()
            next_refresh = self._next_refresh_time(now)

            if next_refresh is None:
                await self._sleep_with_wake(timedelta(minutes=_MIDNIGHT_LOOP_SLEEP_MINUTES))
                continue

            if next_refresh > now:
                await self._wait_until(next_refresh)

            if not self._running:
                break

            self._refresh_due_jobs(self.clock.now())

    def _next_refresh_time(self, now: datetime) -> datetime | None:
        """Return the earliest local midnight at which a refresh is due."""
        candidates: list[datetime] = []

        for job in self.registry.jobs:
            tzinfo = job.trigger.timezone_info
            local_now = now.astimezone(tzinfo)

            if self._last_refresh.get(job.id) != local_now.date():
                return now

            next_midnight_local = datetime.combine(
                local_now.date() + timedelta(days=1),
                time.min,
                tzinfo=tzinfo,
            )

            candidates.append(next_midnight_local.astimezone(UTC))

        return min(candidates) if candidates else None

    def _refresh_due_jobs(self, now: datetime) -> None:
        """Refresh every job whose local date has rolled over since last check."""
        for job in self.registry.jobs:
            local_date = self._local_date(job, now)

            if self._last_refresh.get(job.id) != local_date:
                logger.info("Midnight recalibration for job %s", job.name)
                self._refresh_job(job, now)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _acquire_concurrency_slot(self) -> bool:
        """Acquire a concurrency slot while still respecting stop()."""
        while self._running:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=_SEMAPHORE_POLL_SECONDS,
                )
                return True
            except TimeoutError:
                continue

        return False

    async def _execute_entry(self, entry: QueueEntry) -> None:
        """Execute one queue entry and always release the concurrency slot."""
        try:
            await self._execute(entry.job_id, entry.when, entry.attempt)
        finally:
            self._semaphore.release()

    async def _execute(
        self,
        job_id: uuid.UUID,
        scheduled_for: datetime,
        attempt: int,
    ) -> None:
        """Evaluate conditions and run a single job occurrence."""
        job = self.registry.get_job(job_id)

        if job is None:
            return

        now = self.clock.now()

        # Missed-run protection.
        max_latency = job.max_latency if job.max_latency is not None else self.default_max_latency

        if max_latency is not None and now - scheduled_for > max_latency:
            finished_at = self.clock.now()

            self._record(
                job=job,
                scheduled_for=scheduled_for,
                started_at=now,
                finished_at=finished_at,
                attempt=attempt,
                status="skipped",
                error="missed_run",
            )

            logger.warning(
                "Job %s missed its run by %s; skipping.",
                job.name,
                now - scheduled_for,
            )

            self._schedule_next(
                job,
                after=now,
                attempt=1,
                generation=self._generations.get(job.id, 1),
            )
            return

        context = ExecutionContext(
            job_name=job.name,
            scheduled_for=scheduled_for,
            triggered_at=now,
            attempt=attempt,
            trigger=job.trigger,
            metadata={"tags": tuple(job.tags)},
        )

        started_at = self.clock.now()

        condition_timeout = (
            job.condition_timeout
            if job.condition_timeout is not None
            else self.default_condition_timeout
        )

        conditions_ok = False
        condition_status = "condition_failed"
        condition_error: str | None = None

        try:
            if condition_timeout is not None:
                conditions_ok = await asyncio.wait_for(
                    self._evaluate_conditions(job.trigger.conditions, context),
                    timeout=condition_timeout.total_seconds(),
                )
            else:
                conditions_ok = await self._evaluate_conditions(
                    job.trigger.conditions,
                    context,
                )

            condition_status = "conditions_passed" if conditions_ok else "condition_failed"

        except ConditionEvaluationError as exc:
            condition_status = "condition_error"
            condition_error = str(exc)
            logger.exception("Condition evaluation failed for job %s", job.name)

        except TimeoutError:
            condition_status = "condition_timeout"
            condition_error = "condition evaluation timed out"
            logger.warning(
                "Condition evaluation timed out for job %s after %s",
                job.name,
                condition_timeout,
            )

        if not conditions_ok:
            finished_at = self.clock.now()

            self._record(
                job=job,
                scheduled_for=scheduled_for,
                started_at=started_at,
                finished_at=finished_at,
                attempt=attempt,
                status=condition_status,
                error=condition_error,
            )

            self._schedule_retry(job, self.clock.now(), attempt)
            return

        job_timeout = job.job_timeout if job.job_timeout is not None else self.default_job_timeout

        status = "success"
        error: str | None = None

        try:
            if job_timeout is not None:
                await asyncio.wait_for(
                    self._call_job(job, context),
                    timeout=job_timeout.total_seconds(),
                )
            else:
                await self._call_job(job, context)

        except TimeoutError:
            status = "timeout"
            error = "job timed out"
            logger.error(
                "Job %s timed out after %s",
                job.name,
                job_timeout,
            )

        except Exception as exc:
            status = "error"
            error = repr(exc)
            logger.exception("Job %s raised during execution", job.name)

        finished_at = self.clock.now()

        self._record(
            job=job,
            scheduled_for=scheduled_for,
            started_at=started_at,
            finished_at=finished_at,
            attempt=attempt,
            status=status,
            error=error,
        )

        if status == "success":
            self._schedule_next(
                job,
                after=finished_at,
                attempt=1,
                generation=self._generations.get(job.id, 1),
            )
        else:
            # If the trigger has a fail policy, allow failed job executions
            # to retry using the same policy used for failed conditions.
            if job.trigger.fail_policy is not None:
                self._schedule_retry(job, finished_at, attempt)
            else:
                self._schedule_next(
                    job,
                    after=finished_at,
                    attempt=1,
                    generation=self._generations.get(job.id, 1),
                )

    async def _evaluate_conditions(
        self,
        conditions: tuple[ConditionPlugin, ...],
        context: ExecutionContext,
    ) -> bool:
        """Return True when every condition plugin evaluates truthily."""
        for condition in conditions:
            try:
                result = condition.evaluate(context)

                if inspect.isawaitable(result):
                    result = await result

                if not bool(result):
                    return False

            except Exception as exc:
                raise ConditionEvaluationError(f"Condition failed: {condition!r}") from exc

        return True

    async def _call_job(self, job: JobSpec, context: ExecutionContext) -> None:
        """Invoke *job.func* (sync or async) with the correct signature."""
        args = (context, *job.args) if job.pass_context else job.args

        if inspect.iscoroutinefunction(job.func):
            await job.func(*args, **job.kwargs)
        else:
            await asyncio.to_thread(job.func, *args, **job.kwargs)

    # ------------------------------------------------------------------
    # History / observability
    # ------------------------------------------------------------------

    def _record(
        self,
        *,
        job: JobSpec,
        scheduled_for: datetime,
        started_at: datetime,
        finished_at: datetime,
        attempt: int,
        status: str,
        error: str | None,
    ) -> None:
        """Record an execution result in history and emit a log event."""
        record = ExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            scheduled_for=scheduled_for,
            started_at=started_at,
            finished_at=finished_at,
            attempt=attempt,
            status=status,
            error=error,
        )

        self._history.append(record)

        if status == "success":
            logger.info(
                "job.success name=%s scheduled_for=%s attempt=%s",
                job.name,
                scheduled_for,
                attempt,
            )
        else:
            logger.warning(
                "job.%s name=%s scheduled_for=%s attempt=%s error=%s",
                status,
                job.name,
                scheduled_for,
                attempt,
                error,
            )

    # ------------------------------------------------------------------
    # Sleeping / waiting utilities
    # ------------------------------------------------------------------

    async def _wait_until(self, when: datetime) -> None:
        """Sleep until *when*, waking early if the loop is signalled."""
        while self._running:
            delta = (when - self.clock.now()).total_seconds()

            if delta <= 0:
                return

            self._wake.clear()

            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=min(delta, _MAX_WAIT_SECONDS),
                )

    async def _sleep_with_wake(self, interval: timedelta) -> None:
        """Sleep for *interval* unless the loop is signalled earlier."""
        if interval.total_seconds() <= 0:
            return

        self._wake.clear()

        with suppress(TimeoutError):
            await asyncio.wait_for(
                self._wake.wait(),
                timeout=interval.total_seconds(),
            )
