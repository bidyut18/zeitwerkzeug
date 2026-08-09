"""Lazy schedule builder and fluent trigger API."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as clock_time
from typing import TypeAlias
from zoneinfo import ZoneInfo

from zeitwerkzeug.astro.events import SolarAngle, SolarEvent, SolarTarget
from zeitwerkzeug.astro.location import Location
from zeitwerkzeug.astro.math_engine import next_solar_event_utc
from zeitwerkzeug.exceptions import ScheduleError
from zeitwerkzeug.interfaces import ConditionPlugin

TimeTarget: TypeAlias = SolarTarget | datetime | clock_time | Callable[[datetime], datetime]

LimitTarget: TypeAlias = SolarEvent | SolarAngle | datetime | clock_time | timedelta


def _as_utc(dt: datetime, default_tz: tzinfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=default_tz).astimezone(UTC)
    return dt.astimezone(UTC)


def _next_time_after(
    after_utc: datetime,
    value: clock_time,
    default_tz: tzinfo,
) -> datetime:
    local_after = after_utc.astimezone(default_tz)
    candidate = datetime.combine(local_after.date(), value, tzinfo=default_tz)

    if candidate <= local_after:
        candidate += timedelta(days=1)

    return candidate.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class FailPolicy:
    """Policy describing what to do when conditions fail."""

    retry_interval: timedelta = timedelta(minutes=10)
    limit: LimitTarget | None = None
    max_attempts: int | None = None

    def resolve_limit(
        self,
        after_utc: datetime,
        lazy: LazySchedule,
    ) -> datetime | None:
        """Resolve the policy limit to a UTC datetime, if any."""
        if self.limit is None:
            return None

        limit = self.limit

        if isinstance(limit, timedelta):
            return after_utc + limit

        if isinstance(limit, datetime):
            return _as_utc(limit, lazy.timezone_info)

        if isinstance(limit, clock_time):
            return _next_time_after(after_utc, limit, lazy.timezone_info)

        if isinstance(limit, (SolarEvent, SolarAngle)):
            if lazy.location is None:
                raise ScheduleError("Solar limits require a location on the schedule.")

            return next_solar_event_utc(after_utc, lazy.location, limit)

        raise ScheduleError(f"Unsupported limit target: {limit!r}")


@dataclass(frozen=True, slots=True)
class LazySchedule:
    """A lazily-resolved trigger definition."""

    target: TimeTarget
    location: Location | None = None
    tz: str | None = None
    conditions: tuple[ConditionPlugin, ...] = ()
    fail_policy: FailPolicy | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def timezone_info(self) -> tzinfo:
        """Best-effort timezone for non-solar targets."""
        if self.location is not None:
            return self.location.tzinfo

        if self.tz is not None:
            return ZoneInfo(self.tz)

        return UTC

    def require(self, *conditions: ConditionPlugin) -> LazySchedule:
        """Add one or more required conditions."""
        for condition in conditions:
            if not hasattr(condition, "evaluate"):
                raise TypeError(f"Condition must implement evaluate(): {condition!r}")

        return replace(self, conditions=self.conditions + tuple(conditions))

    def on_fail(
        self,
        *,
        retry_interval_mins: int | float | None = None,
        retry_interval: timedelta | None = None,
        limit: LimitTarget | None = None,
        max_attempts: int | None = None,
    ) -> LazySchedule:
        """Attach a failure/retry policy."""
        if retry_interval is None:
            if retry_interval_mins is None:
                retry_interval = timedelta(minutes=10)
            else:
                retry_interval = timedelta(minutes=retry_interval_mins)

        policy = FailPolicy(
            retry_interval=retry_interval,
            limit=limit,
            max_attempts=max_attempts,
        )

        return replace(self, fail_policy=policy)

    def resolve_after(self, after: datetime) -> datetime:
        """Resolve the next trigger time after the given datetime."""
        after_utc = _as_utc(after, UTC)
        target = self.target

        if isinstance(target, (SolarEvent, SolarAngle)):
            if self.location is None:
                raise ScheduleError("Solar schedules require a location.")

            return next_solar_event_utc(after_utc, self.location, target)

        if isinstance(target, datetime):
            target_utc = _as_utc(target, self.timezone_info)

            if target_utc <= after_utc:
                raise ScheduleError("One-off datetime target is already in the past.")

            return target_utc

        if isinstance(target, clock_time):
            return _next_time_after(after_utc, target, self.timezone_info)

        if callable(target):
            resolved = target(after_utc)
            return _as_utc(resolved, self.timezone_info)

        raise ScheduleError(f"Unsupported schedule target: {target!r}")


class ScheduleBuilder:
    """Entry point for fluent schedule construction."""

    def at(
        self,
        target: TimeTarget,
        *,
        location: Location | None = None,
        tz: str | None = None,
    ) -> LazySchedule:
        """Start a lazy schedule from a target."""
        return LazySchedule(
            target=target,
            location=location,
            tz=tz,
        )


schedule = ScheduleBuilder()
