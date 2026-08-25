"""Test doubles and fakes shared across the zeitwerkzeug test suite.

This module contains no pytest fixtures — only reusable classes and factory
functions. Keeping them separate from ``conftest.py`` makes both easier to
navigate and lets tests import them directly without pulling in pytest.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from zeitwerkzeug.exceptions import ZeitwerkzeugError
from zeitwerkzeug.interfaces import ConditionPlugin, ExecutionContext

# ==============================================================================
# Constants
# ==============================================================================

#: Canonical "now" used throughout the test suite.
DEFAULT_NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
#: Canonical next trigger time used throughout the test suite.
DEFAULT_NEXT_AT = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)


def to_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes; convert aware ones to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# ==============================================================================
# Clock double
# ==============================================================================


@dataclass
class MockClock:
    """Controllable clock standing in for ``SystemClock``."""

    _now: datetime = field(default_factory=lambda: DEFAULT_NOW)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = to_utc(when)


# ==============================================================================
# Trigger double
# ==============================================================================


@dataclass
class MockTrigger:
    """Trigger double yielding a fixed, sorted list of occurrences."""

    next_at: datetime | None = None
    occurrences: list[datetime] | tuple[datetime, ...] | None = None
    conditions: tuple[ConditionPlugin, ...] = ()
    fail_policy: Any = None
    timezone_info: Any = UTC
    raise_on_resolve: bool = False

    def __post_init__(self) -> None:
        if self.next_at is not None and self.occurrences is not None:
            raise ValueError("Provide either next_at or occurrences, not both.")
        if self.occurrences is None:
            raw = [self.next_at] if self.next_at is not None else []
        else:
            raw = list(self.occurrences)
        self._occurrences = sorted(to_utc(when) for when in raw if when is not None)

    def resolve_after(self, after: datetime) -> datetime | None:
        if self.raise_on_resolve:
            raise ZeitwerkzeugError("trigger resolve error")
        after = to_utc(after)
        return next((when for when in self._occurrences if when > after), None)


# ==============================================================================
# Fail-policy double
# ==============================================================================


@dataclass
class FakeFailPolicy:
    """Fail-policy double recording ``resolve_limit`` calls."""

    max_attempts: int | None = 3
    retry_interval: timedelta = timedelta(minutes=1)
    limit: datetime | None = None
    raise_on_resolve_limit: bool = False
    resolve_limit_calls: list[tuple[datetime, object]] = field(default_factory=list)

    def resolve_limit(self, now: datetime, trigger: object) -> datetime | None:
        self.resolve_limit_calls.append((now, trigger))
        if self.raise_on_resolve_limit:
            raise ZeitwerkzeugError("fake resolve_limit failure")
        return self.limit


# ==============================================================================
# Condition doubles
# ==============================================================================


@dataclass
class CountingCondition:
    """Sync condition that counts evaluations."""

    result: bool = True
    call_count: int = 0

    def evaluate(self, context: ExecutionContext) -> bool:
        self.call_count += 1
        return self.result


@dataclass
class AsyncCountingCondition:
    """Async condition that counts evaluations, optionally sleeping."""

    result: bool = True
    delay: float = 0.0
    call_count: int = 0

    async def evaluate(self, context: ExecutionContext) -> bool:
        self.call_count += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


@dataclass
class FailingCondition:
    """Condition that always raises."""

    def evaluate(self, context: ExecutionContext) -> bool:
        raise RuntimeError("condition exploded")


@dataclass
class AlwaysTrueCondition:
    def evaluate(self, context) -> bool:
        return True


# ==============================================================================
# httpx client factories
# ==============================================================================

_RealAsyncClient = httpx.AsyncClient


def make_fake_async_client(payload: dict):
    """Return an ``httpx.AsyncClient`` replacement serving a fixed JSON payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return fake_async_client


def make_fake_error_async_client(status_code: int = 500):
    """Return an ``httpx.AsyncClient`` replacement that always errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return fake_async_client
