"""Job condition evaluation and execution."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

from zeitwerkzeug.daemon.clock import SystemClock
from zeitwerkzeug.daemon.models import ExecutionRecord, JobSpec, QueueEntry
from zeitwerkzeug.daemon.registry import FuzzyCron
from zeitwerkzeug.exceptions import ConditionEvaluationError
from zeitwerkzeug.interfaces import ConditionPlugin, ExecutionContext

logger = logging.getLogger(__name__)


class ExecutionMixin:
    """Provides condition evaluation, job invocation, and history recording."""

    _running: bool
    _semaphore: asyncio.Semaphore
    _available: int
    registry: FuzzyCron
    clock: SystemClock
    _generations: dict[uuid.UUID, int]
    _history: deque[ExecutionRecord]
    default_max_latency: timedelta | None
    default_job_timeout: timedelta | None
    default_condition_timeout: timedelta | None
    _schedule_next: Callable[[JobSpec, datetime, int, int], None]
    _schedule_retry: Callable[[JobSpec, datetime, int], None]
    release_concurrency_slot: Callable[[], None]

    async def _acquire_concurrency_slot(self) -> bool:
        """Acquire a concurrency slot while still respecting stop()."""
        from zeitwerkzeug.daemon.constants import _SEMAPHORE_POLL_SECONDS

        semaphore = self._semaphore
        poll = _SEMAPHORE_POLL_SECONDS

        while self._running:
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=poll)
                self._available -= 1
                return True
            except TimeoutError:
                continue

        return False

    async def _execute_entry(self, entry: QueueEntry) -> None:
        """Execute one queue entry and always release the concurrency slot."""
        try:
            await self._execute(entry.job_id, entry.when, entry.attempt)
        finally:
            self.release_concurrency_slot()

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

        clock = self.clock
        now = clock.now()
        job_name = job.name
        job_uuid = job.id
        generation = self._generations.get(job_uuid, 1)
        trigger = job.trigger

        # Missed-run protection.
        max_latency = job.max_latency if job.max_latency is not None else self.default_max_latency

        if max_latency is not None:
            latency = now - scheduled_for
            if latency > max_latency:
                finished_at = clock.now()

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
                    job_name,
                    latency,
                )

                self._schedule_next(job, now, 1, generation)
                return

        context = ExecutionContext(
            job_name=job_name,
            scheduled_for=scheduled_for,
            triggered_at=now,
            attempt=attempt,
            trigger=trigger,
            metadata={"tags": tuple(job.tags)},
        )

        started_at = clock.now()

        condition_timeout = (
            job.condition_timeout
            if job.condition_timeout is not None
            else self.default_condition_timeout
        )

        conditions_ok = False
        condition_status = "condition_failed"
        condition_error: str | None = None

        try:
            evaluate_coro = self._evaluate_conditions(trigger.conditions, context)

            if condition_timeout is not None:
                conditions_ok = await asyncio.wait_for(
                    evaluate_coro,
                    timeout=condition_timeout.total_seconds(),
                )
            else:
                conditions_ok = await evaluate_coro

            condition_status = "conditions_passed" if conditions_ok else "condition_failed"

        except ConditionEvaluationError as exc:
            condition_status = "condition_error"
            condition_error = str(exc)
            logger.exception("Condition evaluation failed for job %s", job_name)

        except TimeoutError:
            condition_status = "condition_timeout"
            condition_error = "condition evaluation timed out"
            logger.warning(
                "Condition evaluation timed out for job %s after %s",
                job_name,
                condition_timeout,
            )

        if not conditions_ok:
            finished_at = clock.now()

            self._record(
                job=job,
                scheduled_for=scheduled_for,
                started_at=started_at,
                finished_at=finished_at,
                attempt=attempt,
                status=condition_status,
                error=condition_error,
            )

            self._schedule_retry(job, finished_at, attempt)
            return

        job_timeout = job.job_timeout if job.job_timeout is not None else self.default_job_timeout

        status = "success"
        error: str | None = None

        try:
            job_coro = self._call_job(job, context)

            if job_timeout is not None:
                await asyncio.wait_for(
                    job_coro,
                    timeout=job_timeout.total_seconds(),
                )
            else:
                await job_coro

        except TimeoutError:
            status = "timeout"
            error = "job timed out"
            logger.error(
                "Job %s timed out after %s",
                job_name,
                job_timeout,
            )

        except Exception as exc:
            status = "error"
            error = repr(exc)
            logger.exception("Job %s raised during execution", job_name)

        finished_at = clock.now()

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
            self._schedule_next(job, finished_at, 1, generation)
        elif trigger.fail_policy is not None:
            self._schedule_retry(job, finished_at, attempt)
        else:
            self._schedule_next(job, finished_at, 1, generation)

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

                if not result:
                    return False

            except Exception as exc:
                raise ConditionEvaluationError(f"Condition failed: {condition!r}") from exc

        return True

    async def _call_job(self, job: JobSpec, context: ExecutionContext) -> None:
        """Invoke *job.func* (sync or async) with the correct signature."""
        func = job.func
        args = (context, *job.args) if job.pass_context else job.args
        kwargs = job.kwargs

        if inspect.iscoroutinefunction(func):
            await func(*args, **kwargs)
        else:
            result = await asyncio.to_thread(func, *args, **kwargs)

            if inspect.isawaitable(result):
                await result

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
        elif status in ("skipped", "condition_failed"):
            logger.info(
                "job.%s name=%s scheduled_for=%s attempt=%s error=%s",
                status,
                job.name,
                scheduled_for,
                attempt,
                error,
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
