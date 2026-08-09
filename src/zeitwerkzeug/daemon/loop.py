"""Async execution engine for drifting schedules."""

from __future__ import annotations

import asyncio
import heapq
import inspect
import logging
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple

from zeitwerkzeug.daemon.cron import FuzzyCron, JobSpec
from zeitwerkzeug.exceptions import ConditionEvaluationError, ZeitwerkzeugError
from zeitwerkzeug.interfaces import ConditionPlugin, ExecutionContext

logger = logging.getLogger("zeitwerkzeug.daemon")

# ----------------------------------------------------------------------
# Timing constants
# ----------------------------------------------------------------------
_MAX_WAIT_SECONDS = 300.0
_EMPTY_QUEUE_SLEEP_SECONDS = 30
_MIDNIGHT_LOOP_SLEEP_MINUTES = 5


class SystemClock:
    """Default wall-clock time source."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class QueueEntry(NamedTuple):
    """A single item in the execution heap.

    Fields are ordered so that heap comparison (datetime first, then
    generation) matches the original tuple layout exactly.
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
    """

    def __init__(
        self,
        registry: FuzzyCron | None = None,
        *,
        clock: SystemClock | None = None,
        midnight_recalibration: bool = True,
    ) -> None:
        self.registry = registry or FuzzyCron.default()
        self.clock = clock or SystemClock()
        self.midnight_recalibration = midnight_recalibration

        self._queue: list[QueueEntry] = []
        self._generations: dict[uuid.UUID, int] = {}
        self._last_refresh: dict[uuid.UUID, date] = {}
        self._active: set[uuid.UUID] = set()

        self._running = False
        self._wake = asyncio.Event()
        self._until: datetime | None = None

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._running = False
        self._wake.set()

    async def run(self, *, until: datetime | None = None) -> None:
        """Run the daemon until stopped or until an optional deadline."""
        self._running = True
        self._until = until

        self._populate_all(self.clock.now())

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._queue_loop())

            if self.midnight_recalibration:
                tg.create_task(self._midnight_loop())

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
                "Job %s reached max attempts; scheduling next occurrence.", job.name
            )
            self._schedule_next(job, now, attempt=1, generation=generation)
            return

        try:
            limit_utc = policy.resolve_limit(now, job.trigger)
        except ZeitwerkzeugError:
            limit_utc = None

        next_retry = now + policy.retry_interval

        if limit_utc is not None and next_retry > limit_utc:
            logger.info(
                "Job %s retry limit reached; scheduling next occurrence.", job.name
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
        heapq.heappush(self._queue, QueueEntry(when, generation, job_id, attempt))
        self._wake.set()

    def _peek_valid(self) -> QueueEntry | None:
        """Return the earliest queue entry whose generation is still current.

        Stale entries (removed jobs or superseded generations) are discarded.
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
                await self._sleep_with_wake(
                    timedelta(seconds=_EMPTY_QUEUE_SLEEP_SECONDS)
                )
                continue

            when = entry.when
            now = self.clock.now()

            if when > now:
                await self._wait_until(when)
                continue

            heapq.heappop(self._queue)
            await self._execute(entry.job_id, when, entry.attempt)

    async def _midnight_loop(self) -> None:
        """Recalibrate job schedules at local midnight for each timezone."""
        while self._running:
            now = self.clock.now()
            next_refresh = self._next_refresh_time(now)

            if next_refresh is None:
                await self._sleep_with_wake(
                    timedelta(minutes=_MIDNIGHT_LOOP_SLEEP_MINUTES)
                )
                continue

            if next_refresh > now:
                await self._wait_until(next_refresh)

            if not self._running:
                break

            self._refresh_due_jobs(self.clock.now())

    def _next_refresh_time(self, now: datetime) -> datetime | None:
        """Return the earliest local midnight at which a refresh is due.

        If any job has not yet been refreshed for its current local day,
        ``now`` is returned so the refresh happens immediately.
        """
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

        context = ExecutionContext(
            job_name=job.name,
            scheduled_for=scheduled_for,
            triggered_at=now,
            attempt=attempt,
            trigger=job.trigger,
            metadata={"tags": tuple(job.tags)},
        )

        try:
            conditions_ok = await self._evaluate_conditions(
                job.trigger.conditions, context
            )
        except ConditionEvaluationError:
            logger.exception("Condition evaluation failed for job %s", job.name)
            conditions_ok = False

        if conditions_ok:
            try:
                await self._call_job(job, context)
            except Exception:
                logger.exception("Job %s raised during execution", job.name)

            self._schedule_next(
                job,
                after=self.clock.now(),
                attempt=1,
                generation=self._generations.get(job.id, 1),
            )
        else:
            logger.info("Conditions failed for job %s", job.name)
            self._schedule_retry(job, now, attempt)

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
                raise ConditionEvaluationError(
                    f"Condition failed: {condition!r}"
                ) from exc

        return True

    async def _call_job(self, job: JobSpec, context: ExecutionContext) -> None:
        """Invoke *job.func* (sync or async) with the correct signature."""
        args = (context, *job.args) if job.pass_context else job.args

        if inspect.iscoroutinefunction(job.func):
            await job.func(*args, **job.kwargs)
        else:
            await asyncio.to_thread(job.func, *args, **job.kwargs)

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
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=min(delta, _MAX_WAIT_SECONDS),
                )

    async def _sleep_with_wake(self, interval: timedelta) -> None:
        """Sleep for *interval* unless the loop is signalled earlier."""
        self._wake.clear()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self._wake.wait(),
                timeout=interval.total_seconds(),
            )