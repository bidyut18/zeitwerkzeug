"""Built-in condition plugins and combinators."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from zeitwerkzeug.astro.location import Location
from zeitwerkzeug.astro.math_engine import sun_altitude
from zeitwerkzeug.interfaces import ExecutionContext


@dataclass(frozen=True, slots=True)
class AlwaysTrue:
    """Condition that always passes."""

    def evaluate(self, context: ExecutionContext) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SunAltitudeAbove:
    """Require the sun to be above a minimum altitude."""

    location: Location
    min_altitude: float

    def evaluate(self, context: ExecutionContext) -> bool:
        return sun_altitude(context.triggered_at, self.location) >= self.min_altitude


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Require execution inside a local time window."""

    start: clock_time
    end: clock_time
    tz: str | None = None

    @property
    def tzinfo(self) -> datetime.tzinfo:
        if self.tz is None:
            return datetime.UTC
        return ZoneInfo(self.tz)

    def evaluate(self, context: ExecutionContext) -> bool:
        local_time = context.triggered_at.astimezone(self.tzinfo).time()

        if self.start <= self.end:
            return self.start <= local_time < self.end

        # Window crosses midnight.
        return local_time >= self.start or local_time < self.end


@dataclass(frozen=True, slots=True)
class All:
    """Logical AND combinator."""

    conditions: tuple[ConditionLike, ...]

    def evaluate(self, context: ExecutionContext) -> bool:
        return all(condition.evaluate(context) for condition in self.conditions)


@dataclass(frozen=True, slots=True)
class Any:
    """Logical OR combinator."""

    conditions: tuple[ConditionLike, ...]

    def evaluate(self, context: ExecutionContext) -> bool:
        return any(condition.evaluate(context) for condition in self.conditions)


@dataclass(frozen=True, slots=True)
class Not:
    """Logical NOT combinator."""

    condition: ConditionLike

    def evaluate(self, context: ExecutionContext) -> bool:
        return not self.condition.evaluate(context)


ConditionLike = AlwaysTrue | SunAltitudeAbove | TimeWindow | All | Any | Not
