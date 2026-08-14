"""Job scheduling and registry synchronization."""

from __future__ import annotations

import heapq
import logging
import uuid
from asyncio import Event
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from zeitwerkzeug.daemon.clock import _ensure_utc
from zeitwerkzeug.daemon.models import JobSpec, QueueEntry
from zeitwerkzeug.exceptions import ZeitwerkzeugError

if TYPE_CHECKING:
    from zeitwerkzeug.daemon.clock import SystemClock
    from zeitwerkzeug.daemon.registry import FuzzyCron

logger = logging.getLogger(__name__)


class SchedulingMixin:
    """Provides queue management, refresh, and midnight recalibration."""

    if TYPE_CHECKING:
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
        for job in self.registry.jobs:
            if job.id not in self._active:
                self._refresh_job(job, now)

    def _populate_missing(self) -> None:
        """Remove deleted jobs and schedule newly added ones."""
        now = self.clock.now()
        current_ids = {job.id for job in self.registry.jobs}

        for job_id in list(self._active):
            if job_id not in current_ids:
                self._active.discard(job_id)
                self._generations.pop(job_id, None)
                self._last_refresh.pop(job_id, None)

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
    # Midnight recalibration
    # ------------------------------------------------------------------

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
