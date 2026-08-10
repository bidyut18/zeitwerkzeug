"""Shared fixtures for the zeitwerkzeug test suite."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest

from zeitwerkzeug import (
    ClearWeather,
    ExecutionContext,
    FuzzyCron,
    Location,
    PersonaParser,
    StandardWorker,
    schedule,
)
from zeitwerkzeug.daemon.loop import ExecutionLoop
from zeitwerkzeug.exceptions import ZeitwerkzeugError
from zeitwerkzeug.integrations.rate_limit import OpenMeteoRateLimiter
from zeitwerkzeug.interfaces import ConditionPlugin

_RealAsyncClient = httpx.AsyncClient


def _make_fake_async_client(payload: dict):
    """Return a factory that creates a fake httpx.AsyncClient."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return fake_async_client


def _make_fake_error_async_client(status_code: int = 500):
    """Return a factory that creates a fake httpx.AsyncClient that errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return fake_async_client


@pytest.fixture
def noop_job() -> Callable[[], None]:
    """Return a no-op function suitable for job registration."""

    def _job() -> None:
        return None

    return _job


@pytest.fixture
def fuzzy_cron() -> FuzzyCron:
    """Return a fresh, isolated registry."""
    return FuzzyCron()


@pytest.fixture
def utc_noon_trigger():
    """Return a daily-at-12:00-UTC trigger."""
    return schedule.at(time(12, 0), tz="UTC")


@pytest.fixture
def summer_solstice_2026() -> date:
    return date(2026, 6, 21)


@pytest.fixture
def winter_solstice_2026() -> date:
    return date(2026, 12, 21)


@pytest.fixture
def reference_utc() -> datetime:
    return datetime(2026, 8, 9, 5, 0, tzinfo=UTC)


@pytest.fixture
def utc_morning() -> datetime:
    return datetime(2026, 8, 9, 11, 0, tzinfo=UTC)


@pytest.fixture
def utc_afternoon() -> datetime:
    return datetime(2026, 8, 9, 13, 0, tzinfo=UTC)


@pytest.fixture
def greenwich() -> Location:
    return Location(lat=51.4779, lon=-0.0015, timezone="UTC")


@pytest.fixture
def osaka_japan() -> Location:
    return Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")


@pytest.fixture
def polar_location() -> Location:
    return Location(lat=80.0, lon=0.0, timezone="UTC")


@pytest.fixture
def utc_worker() -> StandardWorker:
    return StandardWorker(tz="UTC")


@pytest.fixture
def utc_parser(utc_worker: StandardWorker) -> PersonaParser:
    return PersonaParser(utc_worker)


@pytest.fixture
def execution_context() -> ExecutionContext:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

    return ExecutionContext(
        job_name="test-job",
        scheduled_for=now,
        triggered_at=now,
        attempt=1,
        trigger=trigger,
        metadata={"tags": ()},
    )


class _AlwaysTrueCondition:
    def evaluate(self, context) -> bool:
        return True


@pytest.fixture
def true_condition():
    return _AlwaysTrueCondition()


@pytest.fixture
def rate_limiter() -> OpenMeteoRateLimiter:
    return OpenMeteoRateLimiter(
        max_per_minute=2,
        max_per_hour=10,
        max_per_day=100,
    )


@pytest.fixture
def strict_daily_limiter() -> OpenMeteoRateLimiter:
    return OpenMeteoRateLimiter(
        max_per_minute=100,
        max_per_hour=100,
        max_per_day=2,
    )


@pytest.fixture
def clear_weather_osaka() -> ClearWeather:
    condition = ClearWeather(
        lat=34.6937,
        lon=135.5020,
        max_cloud_cover=20,
    )
    condition.limiter = None
    return condition


@pytest.fixture
def fake_weather_client_factory():
    return _make_fake_async_client


@pytest.fixture
def fake_weather_error_client_factory():
    return _make_fake_error_async_client


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@pytest.fixture(autouse=True)
def _clear_default_registry():
    """Reset process-wide default registry before and after every test."""
    FuzzyCron.reset_default()
    yield
    FuzzyCron.reset_default()


class MockClock:
    """Deterministic clock for testing the execution loop."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = _utc(when)


class FakeFailPolicy:
    """Deterministic fail-policy double for retry tests."""

    def __init__(
        self,
        *,
        max_attempts: int | None = 3,
        retry_interval: timedelta = timedelta(minutes=1),
        limit: datetime | None = None,
        raise_on_resolve_limit: bool = False,
    ) -> None:
        self.max_attempts = max_attempts
        self.retry_interval = retry_interval
        self._limit = limit
        self._raise_on_resolve_limit = raise_on_resolve_limit
        self.resolve_limit_calls: list[tuple[datetime, object]] = []

    def resolve_limit(self, now: datetime, trigger: object) -> datetime | None:
        self.resolve_limit_calls.append((now, trigger))

        if self._raise_on_resolve_limit:
            raise ZeitwerkzeugError("fake resolve_limit failure")

        return self._limit


class MockTrigger:
    """Deterministic trigger stub."""

    def __init__(
        self,
        *,
        next_at: datetime | None = None,
        occurrences: list[datetime] | tuple[datetime, ...] | None = None,
        conditions: tuple[ConditionPlugin, ...] = (),
        fail_policy=None,
        timezone_info=UTC,
        raise_on_resolve: bool = False,
    ) -> None:
        if next_at is not None and occurrences is not None:
            raise ValueError("Provide either next_at or occurrences, not both.")

        if occurrences is None:
            raw_occurrences = [next_at] if next_at is not None else []
        else:
            raw_occurrences = list(occurrences)

        self._occurrences = sorted(_utc(when) for when in raw_occurrences if when is not None)
        self.timezone_info = timezone_info
        self.conditions = tuple(conditions)
        self.fail_policy = fail_policy
        self._raise_on_resolve = raise_on_resolve

    def resolve_after(self, after: datetime) -> datetime | None:
        if self._raise_on_resolve:
            raise ZeitwerkzeugError("trigger resolve error")

        after = _utc(after)

        for when in self._occurrences:
            if when > after:
                return when

        return None


class CountingCondition:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.call_count = 0

    def evaluate(self, context: ExecutionContext) -> bool:
        self.call_count += 1
        return self.result


class AsyncCountingCondition:
    def __init__(self, result: bool = True, delay: float = 0.0) -> None:
        self.result = result
        self.call_count = 0
        self.delay = delay

    async def evaluate(self, context: ExecutionContext) -> bool:
        self.call_count += 1

        if self.delay:
            await asyncio.sleep(self.delay)

        return self.result


class FailingCondition:
    def evaluate(self, context: ExecutionContext) -> bool:
        raise RuntimeError("condition exploded")


@pytest.fixture
def mock_trigger():

    def _factory(*args, **kwargs) -> MockTrigger:
        return MockTrigger(*args, **kwargs)

    return _factory


@pytest.fixture
def fake_clock() -> MockClock:
    return MockClock()


@pytest.fixture
def execution_loop(fake_clock: MockClock, fuzzy_cron: FuzzyCron) -> ExecutionLoop:
    return ExecutionLoop(
        registry=fuzzy_cron,
        clock=fake_clock,
        max_concurrency=2,
        history_limit=10,
    )


@pytest.fixture
def register_job(fuzzy_cron: FuzzyCron):

    def _register(func, trigger=None, *, name: str = "test-job", **kwargs):
        if trigger is None:
            trigger = MockTrigger(next_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC))

        return fuzzy_cron.register(
            func,
            trigger,
            name=name,
            **kwargs,
        )

    return _register


@pytest.fixture
def fake_fail_policy():

    def _factory(
        *,
        max_attempts: int | None = 3,
        retry_interval: timedelta = timedelta(minutes=1),
        limit: datetime | None = None,
        raise_on_resolve_limit: bool = False,
    ) -> FakeFailPolicy:
        return FakeFailPolicy(
            max_attempts=max_attempts,
            retry_interval=retry_interval,
            limit=limit,
            raise_on_resolve_limit=raise_on_resolve_limit,
        )

    return _factory
