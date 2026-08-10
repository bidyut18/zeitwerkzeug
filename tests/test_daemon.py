from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from tests.conftest import (
    AsyncCountingCondition,
    CountingCondition,
    FailingCondition,
    MockTrigger,
)

from zeitwerkzeug import FuzzyCron
from zeitwerkzeug.daemon.loop import (
    ExecutionLoop,
    ExecutionRecord,
    QueueEntry,
    SystemClock,
    _ensure_utc,
)
from zeitwerkzeug.exceptions import ConditionEvaluationError, JobError
from zeitwerkzeug.interfaces import ExecutionContext

# ==============================================================================
# FuzzyCron contract & edge cases
# ==============================================================================


def test_registry_can_add_and_get_job(
    fuzzy_cron: FuzzyCron,
    utc_noon_trigger,
    noop_job,
) -> None:
    registered = fuzzy_cron.register(noop_job, utc_noon_trigger, name="test-job")

    assert fuzzy_cron.get_job(registered.id) is registered
    assert len(fuzzy_cron) == 1
    assert registered.id in fuzzy_cron


def test_registry_can_remove_job(
    fuzzy_cron: FuzzyCron,
    utc_noon_trigger,
    noop_job,
) -> None:
    registered = fuzzy_cron.register(noop_job, utc_noon_trigger, name="test-job")

    fuzzy_cron.remove(registered.id)

    assert fuzzy_cron.get_job(registered.id) is None
    assert len(fuzzy_cron) == 0


def test_registry_add_job_and_remove_job_classmethods(noop_job, utc_noon_trigger) -> None:
    job = FuzzyCron.add_job(noop_job, utc_noon_trigger, name="class-job")

    assert FuzzyCron.default().get_job(job.id) is job

    FuzzyCron.remove_job(job.id)

    assert FuzzyCron.default().get_job(job.id) is None


def test_registry_accepts_protocol_trigger(
    fuzzy_cron: FuzzyCron,
    noop_job,
) -> None:
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

    job = fuzzy_cron.register(noop_job, trigger, name="protocol-trigger")

    assert fuzzy_cron.get_job(job.id) is job


def test_registry_rejects_non_trigger(
    fuzzy_cron: FuzzyCron,
    noop_job,
) -> None:
    with pytest.raises(JobError):
        fuzzy_cron.register(noop_job, object(), name="bad-trigger")


def test_registry_name_fallback(fuzzy_cron: FuzzyCron, utc_noon_trigger) -> None:
    def named_function():
        pass

    job1 = fuzzy_cron.register(named_function, utc_noon_trigger, name=None)
    assert job1.name == "named_function"

    lambda_job = fuzzy_cron.register(lambda: None, utc_noon_trigger, name=None)
    assert lambda_job.name.startswith("job-")


def test_registered_kwargs_are_immutable(
    fuzzy_cron: FuzzyCron,
    noop_job,
) -> None:
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

    job = fuzzy_cron.register(
        noop_job,
        trigger,
        name="immutable-kwargs",
        kwargs={"a": 1},
    )

    with pytest.raises(TypeError):
        job.kwargs["a"] = 2


def test_registry_tags_normalization(fuzzy_cron: FuzzyCron, utc_noon_trigger, noop_job) -> None:
    job_list_tags = fuzzy_cron.register(noop_job, utc_noon_trigger, tags=["tag1", "tag2"])
    assert job_list_tags.tags == frozenset({"tag1", "tag2"})

    f_tags = frozenset({"tag3"})
    job_frozen_tags = fuzzy_cron.register(noop_job, utc_noon_trigger, tags=f_tags)
    assert job_frozen_tags.tags is f_tags


def test_registry_revision_tracks_mutations(
    fuzzy_cron: FuzzyCron,
    noop_job,
) -> None:
    assert fuzzy_cron.revision == 0

    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

    job = fuzzy_cron.register(noop_job, trigger, name="revision-job")
    assert fuzzy_cron.revision == 1

    fuzzy_cron.remove(job.id)
    assert fuzzy_cron.revision == 2

    fuzzy_cron.clear()
    assert fuzzy_cron.revision == 2

    job2 = fuzzy_cron.register(noop_job, trigger, name="revision-job-2")
    fuzzy_cron.remove(job2.id)
    assert fuzzy_cron.revision == 3

    fuzzy_cron.clear()
    assert fuzzy_cron.revision == 4


# ==============================================================================
# ExecutionLoop construction and public metrics
# ==============================================================================


def test_loop_construction_defaults() -> None:
    loop = ExecutionLoop()

    assert loop.max_concurrency == 32
    assert loop.default_job_timeout == timedelta(minutes=5)
    assert loop.default_condition_timeout == timedelta(seconds=30)
    assert loop.midnight_recalibration is True

    stats = loop.stats()
    assert stats["running"] is False
    assert stats["max_concurrency"] == 32
    assert stats["available_concurrency"] == 32


def test_loop_construction_custom() -> None:
    loop = ExecutionLoop(
        max_concurrency=5,
        default_job_timeout=timedelta(minutes=1),
        midnight_recalibration=False,
        history_limit=50,
    )

    assert loop.max_concurrency == 5
    assert loop.default_job_timeout == timedelta(minutes=1)
    assert loop.midnight_recalibration is False
    assert loop.history_limit == 50


def test_loop_construction_invalid_concurrency() -> None:
    with pytest.raises(ValueError, match="max_concurrency must be at least 1"):
        ExecutionLoop(max_concurrency=0)


def test_loop_construction_invalid_history() -> None:
    with pytest.raises(ValueError, match="history_limit must be zero or greater"):
        ExecutionLoop(history_limit=-1)


def test_stop_sets_running_false(execution_loop: ExecutionLoop) -> None:
    execution_loop._running = True

    execution_loop.stop()

    assert execution_loop.stats()["running"] is False


def test_stats_returns_metrics(execution_loop: ExecutionLoop) -> None:
    assert execution_loop.stats() == {
        "running": False,
        "queue_depth": 0,
        "active_jobs": 0,
        "inflight_jobs": 0,
        "history_size": 0,
        "max_concurrency": 2,
        "available_concurrency": 2,
    }


def test_system_clock_returns_utc() -> None:
    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is UTC


# ==============================================================================
# Timing utilities
# ==============================================================================


def test_ensure_utc_naive() -> None:
    naive = datetime(2026, 8, 9, 12, 0)

    assert _ensure_utc(naive).tzinfo is UTC


def test_ensure_utc_aware() -> None:
    est = datetime(2026, 8, 9, 7, 0, tzinfo=timezone(timedelta(hours=-5)))

    result = _ensure_utc(est)

    assert result.tzinfo is UTC
    assert result.hour == 12


def test_execution_record_duration() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    record = ExecutionRecord(
        job_id=uuid.uuid4(),
        job_name="test",
        scheduled_for=now,
        started_at=now,
        finished_at=now + timedelta(seconds=5),
        attempt=1,
        status="success",
    )

    assert record.duration == timedelta(seconds=5)


# ==============================================================================
# Queue validity and generation invalidation
# ==============================================================================


def test_refresh_job_schedules_first_occurrence(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    when = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=when)
    job = register_job(lambda: None, trigger)

    execution_loop.refresh_job(job.id)

    entry = execution_loop.peek_next()

    assert entry is not None
    assert entry == QueueEntry(when, 1, job.id, 1)
    assert execution_loop.queue_snapshot == (entry,)


def test_refresh_job_bumps_generation_and_discards_stale_entry(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    when = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=when)
    job = register_job(lambda: None, trigger)

    execution_loop.refresh_job(job.id)
    first = execution_loop.peek_next()

    execution_loop.refresh_job(job.id)
    second = execution_loop.peek_next()

    assert first is not None
    assert second is not None
    assert second.generation == first.generation + 1
    assert execution_loop.queue_snapshot == (second,)


def test_peek_next_discards_missing_job(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    when = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=when)
    job = register_job(lambda: None, trigger)

    execution_loop.refresh_job(job.id)

    execution_loop.registry.remove(job.id)

    assert execution_loop.peek_next() is None
    assert execution_loop.queue_snapshot == ()


# ==============================================================================
# Registry synchronization
# ==============================================================================


def test_sync_registry_activates_new_jobs(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    when = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=when)
    job = register_job(lambda: None, trigger)

    execution_loop.sync_registry()

    entry = execution_loop.peek_next()

    assert entry is not None
    assert entry.job_id == job.id
    assert entry.when == when
    assert entry.generation == 1
    assert entry.attempt == 1


def test_sync_registry_removes_deleted_jobs(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    when = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=when)
    job = register_job(lambda: None, trigger)

    execution_loop.sync_registry()
    assert execution_loop.peek_next() is not None

    execution_loop.registry.remove(job.id)
    execution_loop.sync_registry()

    assert execution_loop.peek_next() is None
    assert execution_loop.stats()["active_jobs"] == 0


def test_sync_registry_does_not_duplicate_when_unchanged(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    when = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=when)
    register_job(lambda: None, trigger)

    execution_loop.sync_registry()
    first_snapshot = execution_loop.queue_snapshot

    execution_loop.sync_registry()
    second_snapshot = execution_loop.queue_snapshot

    assert first_snapshot == second_snapshot
    assert len(second_snapshot) == 1


def test_refresh_job_logs_warning_when_no_future_occurrence(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
    caplog,
) -> None:
    trigger = MockTrigger()
    job = register_job(lambda: None, trigger, name="unschedulable")

    with caplog.at_level("WARNING"):
        execution_loop.refresh_job(job.id)

    assert "Could not schedule job" in caplog.text
    assert execution_loop.queue_snapshot == ()


def test_schedule_next_handles_zeitwerkzeug_error(
    execution_loop: ExecutionLoop,
    register_job,
    caplog,
) -> None:
    trigger = MockTrigger(raise_on_resolve=True)
    job = register_job(lambda: None, trigger, name="error-trigger-job")

    with caplog.at_level("WARNING"):
        execution_loop.refresh_job(job.id)

    assert "Could not schedule job error-trigger-job" in caplog.text


# ==============================================================================
# Condition evaluation
# ==============================================================================


@pytest.mark.asyncio
async def test_evaluate_conditions_all_true(
    execution_loop: ExecutionLoop,
    execution_context: ExecutionContext,
) -> None:
    c1 = CountingCondition(True)
    c2 = CountingCondition(True)

    result = await execution_loop._evaluate_conditions((c1, c2), execution_context)

    assert result is True
    assert c1.call_count == 1
    assert c2.call_count == 1


@pytest.mark.asyncio
async def test_evaluate_conditions_short_circuits(
    execution_loop: ExecutionLoop,
    execution_context: ExecutionContext,
) -> None:
    c1 = CountingCondition(False)
    c2 = CountingCondition(True)

    result = await execution_loop._evaluate_conditions((c1, c2), execution_context)

    assert result is False
    assert c1.call_count == 1
    assert c2.call_count == 0


@pytest.mark.asyncio
async def test_evaluate_conditions_async_condition(
    execution_loop: ExecutionLoop,
    execution_context: ExecutionContext,
) -> None:
    condition = AsyncCountingCondition(result=True)

    result = await execution_loop._evaluate_conditions((condition,), execution_context)

    assert result is True
    assert condition.call_count == 1


@pytest.mark.asyncio
async def test_evaluate_conditions_raises(
    execution_loop: ExecutionLoop,
    execution_context: ExecutionContext,
) -> None:
    with pytest.raises(ConditionEvaluationError):
        await execution_loop._evaluate_conditions((FailingCondition(),), execution_context)


# ==============================================================================
# Job invocation
# ==============================================================================


@pytest.mark.asyncio
async def test_call_job_sync(
    execution_loop: ExecutionLoop,
    register_job,
    execution_context: ExecutionContext,
) -> None:
    calls = []

    def sync_func(a: int, b: str) -> None:
        calls.append((a, b))

    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(sync_func, trigger, args=(1,), kwargs={"b": "x"})

    await execution_loop._call_job(job, execution_context)

    assert calls == [(1, "x")]


@pytest.mark.asyncio
async def test_call_job_async(
    execution_loop: ExecutionLoop,
    register_job,
    execution_context: ExecutionContext,
) -> None:
    calls = []

    async def async_func(a: int) -> None:
        calls.append(a)

    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(async_func, trigger, args=(42,))

    await execution_loop._call_job(job, execution_context)

    assert calls == [42]


@pytest.mark.asyncio
async def test_call_job_with_context(
    execution_loop: ExecutionLoop,
    register_job,
    execution_context: ExecutionContext,
) -> None:
    calls = []

    def sync_func(ctx: ExecutionContext, a: int) -> None:
        calls.append((ctx.job_name, a))

    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(sync_func, trigger, args=(99,), pass_context=True)

    await execution_loop._call_job(job, execution_context)

    assert calls == [("test-job", 99)]


@pytest.mark.asyncio
async def test_call_job_sync_returning_awaitable(
    execution_loop: ExecutionLoop,
    register_job,
    execution_context: ExecutionContext,
) -> None:
    calls = []

    async def inner() -> None:
        calls.append(1)

    def factory():
        return inner()

    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(factory, trigger)

    await execution_loop._call_job(job, execution_context)

    assert calls == [1]


# ==============================================================================
# History / observability
# ==============================================================================


def test_record_adds_to_history(
    execution_loop: ExecutionLoop,
    register_job,
) -> None:
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(lambda: None, trigger, name="rec-job")

    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    execution_loop._record(
        job=job,
        scheduled_for=now,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        attempt=1,
        status="success",
        error=None,
    )

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].job_name == "rec-job"
    assert history[0].status == "success"
    assert history[0].duration == timedelta(seconds=1)


def test_record_respects_limit(
    execution_loop: ExecutionLoop,
    register_job,
) -> None:
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(lambda: None, trigger)

    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    for attempt in range(15):
        execution_loop._record(
            job=job,
            scheduled_for=now,
            started_at=now,
            finished_at=now,
            attempt=attempt,
            status="success",
            error=None,
        )

    history = execution_loop.history

    assert len(history) == 10
    assert history[-1].attempt == 14


# ==============================================================================
# _execute — missed-run protection
# ==============================================================================


@pytest.mark.asyncio
async def test_execute_skips_missed_run(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

    job = register_job(
        lambda: None,
        trigger,
        name="missed-job",
        max_latency=timedelta(minutes=5),
    )

    execution_loop._generations[job.id] = 1

    scheduled = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)
    fake_clock.set(datetime(2026, 8, 9, 11, 10, tzinfo=UTC))

    await execution_loop._execute(job.id, scheduled, attempt=1)

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "skipped"
    assert history[0].error == "missed_run"


@pytest.mark.asyncio
async def test_execute_does_not_skip_if_within_latency(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    calls = []

    def job_func() -> None:
        calls.append(1)

    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

    job = register_job(
        job_func,
        trigger,
        name="on-time-job",
        max_latency=timedelta(minutes=5),
    )

    execution_loop._generations[job.id] = 1

    scheduled = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    fake_clock.set(datetime(2026, 8, 9, 12, 3, tzinfo=UTC))

    await execution_loop._execute(job.id, scheduled, attempt=1)

    assert calls == [1]

    history = execution_loop.history

    assert history[0].status == "success"


# ==============================================================================
# _execute — condition paths
# ==============================================================================


@pytest.mark.asyncio
async def test_execute_condition_failed_schedules_retry(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    next_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)

    trigger = MockTrigger(
        next_at=next_at,
        conditions=(CountingCondition(result=False),),
    )

    job = register_job(lambda: None, trigger, name="cond-fail-job")
    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "condition_failed"


@pytest.mark.asyncio
async def test_execute_condition_timeout(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    next_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)

    trigger = MockTrigger(
        next_at=next_at,
        conditions=(AsyncCountingCondition(result=True, delay=10),),
    )

    job = register_job(
        lambda: None,
        trigger,
        name="cond-timeout-job",
        condition_timeout=timedelta(milliseconds=50),
    )

    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "condition_timeout"


@pytest.mark.asyncio
async def test_execute_condition_error(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    next_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)

    trigger = MockTrigger(
        next_at=next_at,
        conditions=(FailingCondition(),),
    )

    job = register_job(lambda: None, trigger, name="cond-error-job")
    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "condition_error"


# ==============================================================================
# _execute — job execution outcomes
# ==============================================================================


@pytest.mark.asyncio
async def test_execute_job_success(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    calls = []

    def job_func() -> None:
        calls.append(1)

    next_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=next_at)

    job = register_job(job_func, trigger, name="success-job")
    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    assert calls == [1]

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "success"


@pytest.mark.asyncio
async def test_execute_job_timeout(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    async def slow_job() -> None:
        await asyncio.sleep(10)

    next_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=next_at)

    job = register_job(
        slow_job,
        trigger,
        name="timeout-job",
        job_timeout=timedelta(milliseconds=50),
    )

    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "timeout"


@pytest.mark.asyncio
async def test_execute_job_error(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    def bad_job() -> None:
        raise RuntimeError("boom")

    next_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=next_at)

    job = register_job(bad_job, trigger, name="error-job")
    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    history = execution_loop.history

    assert len(history) == 1
    assert history[0].status == "error"


# ==============================================================================
# Retry & Concurrency edge cases
# ==============================================================================


@pytest.mark.asyncio
async def test_fail_policy_first_failure_schedules_retry(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
    fake_fail_policy,
) -> None:
    def bad_job() -> None:
        raise RuntimeError("boom")

    policy = fake_fail_policy(
        max_attempts=3,
        retry_interval=timedelta(minutes=1),
    )

    trigger = MockTrigger(
        next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC),
        fail_policy=policy,
    )

    job = register_job(bad_job, trigger, name="retry-job")
    execution_loop._generations[job.id] = 1

    await execution_loop._execute(job.id, fake_clock.now(), attempt=1)

    entries = execution_loop.queue_snapshot

    assert len(entries) == 1
    assert entries[0].attempt == 2
    assert entries[0].when == fake_clock.now() + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_acquire_concurrency_slot_success(execution_loop: ExecutionLoop) -> None:
    acquired = await execution_loop.acquire_concurrency_slot()

    assert acquired is True
    assert execution_loop.available_concurrency == 1

    execution_loop.release_concurrency_slot()

    assert execution_loop.available_concurrency == 2


def test_release_concurrency_slot_does_not_over_release(
    execution_loop: ExecutionLoop,
) -> None:
    assert execution_loop.available_concurrency == 2

    execution_loop.release_concurrency_slot()

    assert execution_loop.available_concurrency == 2


@pytest.mark.asyncio
async def test_execute_entry_releases_semaphore(
    execution_loop: ExecutionLoop,
    fake_clock,
    register_job,
) -> None:
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))
    job = register_job(lambda: None, trigger, name="entry-job")

    execution_loop._generations[job.id] = 1

    acquired = await execution_loop.acquire_concurrency_slot()
    assert acquired is True
    assert execution_loop.available_concurrency == 1

    entry = QueueEntry(fake_clock.now(), 1, job.id, 1)

    await execution_loop._execute_entry(entry)

    assert execution_loop.available_concurrency == 2
