"""Shared fixtures for the zeitwerkzeug test suite."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time

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
from zeitwerkzeug.integrations.rate_limit import OpenMeteoRateLimiter

# ----------------------------------------------------------------------
# httpx shims — capture the real class BEFORE any monkeypatching happens
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# Simple callables
# ----------------------------------------------------------------------


@pytest.fixture
def noop_job() -> Callable[[], None]:
    """Return a no-op function suitable for job registration."""

    def _job() -> None:
        return None

    return _job


# ----------------------------------------------------------------------
# Scheduler / registry
# ----------------------------------------------------------------------


@pytest.fixture
def fuzzy_cron() -> FuzzyCron:
    """Return a fresh, empty FuzzyCron registry."""
    return FuzzyCron()


@pytest.fixture
def utc_noon_trigger():
    """Return a daily-at-12:00-UTC trigger."""
    return schedule.at(time(12, 0), tz="UTC")


# ----------------------------------------------------------------------
# Dates & datetimes
# ----------------------------------------------------------------------


@pytest.fixture
def summer_solstice_2026() -> date:
    """June 21st 2026 — useful for northern-hemisphere solar tests."""
    return date(2026, 6, 21)


@pytest.fixture
def winter_solstice_2026() -> date:
    """December 21st 2026 — useful for polar-night tests."""
    return date(2026, 12, 21)


@pytest.fixture
def reference_utc() -> datetime:
    """A fixed UTC datetime (2026-08-09 05:00) used in persona tests."""
    return datetime(2026, 8, 9, 5, 0, tzinfo=UTC)


@pytest.fixture
def utc_morning() -> datetime:
    """2026-08-09 11:00 UTC — before the default noon trigger."""
    return datetime(2026, 8, 9, 11, 0, tzinfo=UTC)


@pytest.fixture
def utc_afternoon() -> datetime:
    """2026-08-09 13:00 UTC — after the default noon trigger."""
    return datetime(2026, 8, 9, 13, 0, tzinfo=UTC)


# ----------------------------------------------------------------------
# Locations
# ----------------------------------------------------------------------


@pytest.fixture
def greenwich() -> Location:
    """Royal Observatory, Greenwich (UTC)."""
    return Location(lat=51.4779, lon=-0.0015, timezone="UTC")


@pytest.fixture
def osaka_japan() -> Location:
    """Osaka, Japan (Asia/Tokyo)."""
    return Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")


@pytest.fixture
def polar_location() -> Location:
    """High Arctic — useful for polar-night / midnight-sun tests."""
    return Location(lat=80.0, lon=0.0, timezone="UTC")


# ----------------------------------------------------------------------
# Personas
# ----------------------------------------------------------------------


@pytest.fixture
def utc_worker() -> StandardWorker:
    """A StandardWorker profile aligned to UTC."""
    return StandardWorker(tz="UTC")


@pytest.fixture
def utc_parser(utc_worker: StandardWorker) -> PersonaParser:
    """A PersonaParser backed by a UTC StandardWorker."""
    return PersonaParser(utc_worker)


# ----------------------------------------------------------------------
# Execution context
# ----------------------------------------------------------------------


@pytest.fixture
def execution_context() -> ExecutionContext:
    """Return a minimal execution context for condition testing."""
    now = datetime.now(UTC)
    return ExecutionContext(
        job_name="test-job",
        scheduled_for=now,
        triggered_at=now,
        attempt=1,
    )


# ----------------------------------------------------------------------
# Conditions
# ----------------------------------------------------------------------


class _AlwaysTrueCondition:
    """A condition plugin that always evaluates to True."""

    def evaluate(self, context) -> bool:
        return True


@pytest.fixture
def true_condition():
    """Return a condition instance that always passes."""
    return _AlwaysTrueCondition()


# ----------------------------------------------------------------------
# Rate limiter
# ----------------------------------------------------------------------


@pytest.fixture
def rate_limiter() -> OpenMeteoRateLimiter:
    """Return a rate limiter with a tight per-minute cap for unit tests."""
    return OpenMeteoRateLimiter(
        max_per_minute=2,
        max_per_hour=10,
        max_per_day=100,
    )


@pytest.fixture
def strict_daily_limiter() -> OpenMeteoRateLimiter:
    """Return a rate limiter with a tight per-day cap for unit tests."""
    return OpenMeteoRateLimiter(
        max_per_minute=100,
        max_per_hour=100,
        max_per_day=2,
    )


# ----------------------------------------------------------------------
# Weather
# ----------------------------------------------------------------------


@pytest.fixture
def clear_weather_osaka() -> ClearWeather:
    """Return a ClearWeather instance for Osaka with the rate limiter disabled."""
    condition = ClearWeather(
        lat=34.6937,
        lon=135.5020,
        max_cloud_cover=20,
    )
    condition.limiter = None
    return condition


@pytest.fixture
def fake_weather_client_factory():
    """Return a factory for fake Open-Meteo HTTP clients.

    Usage in a test::

        monkeypatch.setattr(
            "zeitwerkzeug.integrations.weather.httpx.AsyncClient",
            fake_weather_client_factory({"current_weather": {"cloudcover": 10}}),
        )
    """
    return _make_fake_async_client


@pytest.fixture
def fake_weather_error_client_factory():
    """Return a factory for fake Open-Meteo HTTP clients that return errors.

    Usage in a test::

        monkeypatch.setattr(
            "zeitwerkzeug.integrations.weather.httpx.AsyncClient",
            fake_weather_error_client_factory(500),
        )
    """
    return _make_fake_error_async_client