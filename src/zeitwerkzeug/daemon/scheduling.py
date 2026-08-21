"""Job scheduling and registry synchronization."""

from __future__ import annotations

import heapq
import logging
import uuid
from asyncio import Event
from datetime import UTC, date, datetime, time, timedelta

from zeitwerkzeug.daemon.clock import SystemClock, _ensure_utc
from zeitwerkzeug.daemon.models import JobSpec, QueueEntry
from zeitwerkzeug.daemon.registry import FuzzyCron
from zeitwerkzeug.exceptions import ZeitwerkzeugError

logger = logging.getLogger(__name__)


class SchedulingMixin:
    """Provides queue management, refresh, and midnight recalibration."""

    _queue: list[QueueEntry]
    _generations: dict[uuid.UUID, int]
    _active: set[uuid.UUID]
    _last_refresh: dict[uuid.UUID, date]
    registry: FuzzyCron
    clock: SystemClock
    _running: bool
    _wake: Event

    # ------------------------------------------------------------------
    # Registry sync
    # ------------------------------------------------------------------

    def _populate_all(self, now: datetime) -> None:
        """Schedule every job that is not yet active."""
        _active = self._active
        for job in self.registry.jobs:
            if job.id not in _active:
                self._refresh_job(job, now)

    def _populate_missing(self) -> None:
        """Remove deleted jobs and schedule newly added ones."""
        now = self.clock.now()
        current_ids = {job.id for job in self.registry.jobs}
        _active = self._active
        _generations = self._generations
        _last_refresh = self._last_refresh

        # Set difference is faster than list() + conditional discard loop
        for job_id in _active - current_ids:
            _active.discard(job_id)
            _generations.pop(job_id, None)
            _last_refresh.pop(job_id, None)

        for job in self.registry.jobs:
            if job.id not in _active:
                self._refresh_job(job, now)

    def _refresh_job(self, job: JobSpec, now: datetime) -> None:
        """Bump a job's generation and schedule its next occurrence."""
        job_id = job.id
        generation = self._generations.get(job_id, 0) + 1

        self._generations[job_id] = generation
        self._active.add(job_id)
        self._last_refresh[job_id] = self._local_date(job, now)

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

        if next_at is None:
            logger.warning("Could not schedule job %s: no future occurrence", job.name)
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
            self._schedule_next(job, now, attempt=attempt + 1, generation=generation)
            return

        max_attempts = getattr(policy, "max_attempts", None)
        if max_attempts is not None and attempt >= max_attempts:
            logger.info(
                "Job %s reached max attempts; scheduling next occurrence.",
                job.name,
            )
            self._schedule_next(job, now, attempt=1, generation=generation)
            return

        retry_interval = getattr(policy, "retry_interval", None)
        if retry_interval is None:
            self._schedule_next(job, now, attempt=attempt + 1, generation=generation)
            return

        self._push(now + retry_interval, job.id, attempt + 1, generation)

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
        _queue = self._queue
        _generations = self._generations
        registry_get_job = self.registry.get_job

        while _queue:
            entry = _queue[0]
            job = registry_get_job(entry.job_id)
            current_generation = _generations.get(entry.job_id)

            if job is None or current_generation != entry.generation:
                heapq.heappop(_queue)
                continue

            return entry

        return None

    # ------------------------------------------------------------------
    # Midnight recalibration
    # ------------------------------------------------------------------

    def _next_refresh_time(self, now: datetime) -> datetime | None:
        """Return the earliest local midnight at which a refresh is due."""
        earliest: datetime | None = None
        last_refresh = self._last_refresh

        for job in self.registry.jobs:
            tzinfo = job.trigger.timezone_info
            local_now = now.astimezone(tzinfo)
            local_date = local_now.date()

            if last_refresh.get(job.id) != local_date:
                return now

            next_midnight_local = datetime.combine(
                local_date + timedelta(days=1),
                time.min,
                tzinfo=tzinfo,
            )

            next_midnight_utc = next_midnight_local.astimezone(UTC)
            if earliest is None or next_midnight_utc < earliest:
                earliest = next_midnight_utc

        return earliest

    def _refresh_due_jobs(self, now: datetime) -> None:
        """Refresh every job whose local date has rolled over since last check."""
        _local_date = self._local_date
        last_refresh = self._last_refresh

        for job in self.registry.jobs:
            if last_refresh.get(job.id) != _local_date(job, now):
                logger.info("Midnight recalibration for job %s", job.name)
                self._refresh_job(job, now)
