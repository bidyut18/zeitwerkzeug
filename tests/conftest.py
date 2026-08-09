"""Shared fixtures for the zeitwerkzeug test suite."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time

import pytest

from zeitwerkzeug import FuzzyCron, Location, PersonaParser, StandardWorker, schedule


@pytest.fixture
def noop_job() -> Callable[[], None]:
    """Return a no-op function suitable for job registration."""

    def _job() -> None:
        return None

    return _job



@pytest.fixture
def fuzzy_cron() -> FuzzyCron:
    """Return a fresh, empty FuzzyCron registry."""
    return FuzzyCron()


@pytest.fixture
def utc_noon_trigger():
    """Return a daily-at-12:00-UTC trigger."""
    return schedule.at(time(12, 0), tz="UTC")


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



@pytest.fixture
def greenwich() -> Location:
    """Royal Observatory, Greenwich (UTC)."""
    return Location(lat=51.4779, lon=-0.0015, timezone="UTC")


@pytest.fixture
def delhi() -> Location:
    """New Delhi, India (Asia/Kolkata)."""
    return Location(lat=28.6139, lon=77.2090, timezone="Asia/Kolkata")


@pytest.fixture
def polar_location() -> Location:
    """High Arctic — useful for polar-night / midnight-sun tests."""
    return Location(lat=80.0, lon=0.0, timezone="UTC")



@pytest.fixture
def utc_worker() -> StandardWorker:
    """A StandardWorker profile aligned to UTC."""
    return StandardWorker(tz="UTC")


@pytest.fixture
def utc_parser(utc_worker: StandardWorker) -> PersonaParser:
    """A PersonaParser backed by a UTC StandardWorker."""
    return PersonaParser(utc_worker)


class _AlwaysTrueCondition:
    """A condition plugin that always evaluates to True."""

    def evaluate(self, context) -> bool:
        return True


@pytest.fixture
def true_condition():
    """Return a condition instance that always passes."""
    return _AlwaysTrueCondition()
