"""Persona profiles that model human-relative daily rhythms."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from datetime import time as clock_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zeitwerkzeug.exceptions import PersonaError


def _resolve_tzinfo(name: str) -> ZoneInfo | timezone:
    if name == "UTC" or name in {"Etc/UTC", "Etc/GMT"}:
        return UTC
    return ZoneInfo(name)


@dataclass(frozen=True, slots=True)
class TimeBlock:
    """Concrete time window."""

    start: datetime
    end: datetime
    label: str = ""

    def to_utc(self) -> TimeBlock:
        """Return a UTC-normalized copy."""
        return TimeBlock(
            start=self.start.astimezone(UTC),
            end=self.end.astimezone(UTC),
            label=self.label,
        )

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    """Base persona profile.

    The profile maps human anchors like wake/sleep into concrete datetimes.
    """

    wake: clock_time = clock_time(7, 0)
    sleep: clock_time = clock_time(23, 0)
    tz: str = "UTC"
    weekend_wake_shift: timedelta = timedelta(hours=1)
    weekend_sleep_shift: timedelta = timedelta(hours=1)
    _tzinfo: ZoneInfo = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "_tzinfo", _resolve_tzinfo(self.tz))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Invalid timezone: {self.tz!r}") from exc

    @property
    def tzinfo(self) -> ZoneInfo | timezone:
        return self._tzinfo

    def _localize(self, dt: datetime) -> datetime:
        """Return *dt* in the profile's timezone, avoiding copies when possible."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.tzinfo)
        if dt.tzinfo is self.tzinfo:
            return dt
        return dt.astimezone(self.tzinfo)

    def _is_weekend(self, local_date: datetime) -> bool:
        return local_date.weekday() >= 5

    def _wake_datetime_localized(self, reference: datetime) -> datetime:
        """Return the wake anchor for *reference*'s local date.

        *reference* is assumed to already be in the profile's timezone.
        """
        shift = self.weekend_wake_shift if self._is_weekend(reference) else timedelta()
        return datetime.combine(reference.date(), self.wake, tzinfo=self.tzinfo) + shift

    def wake_datetime(self, reference: datetime) -> datetime:
        """Return the wake anchor for the reference local date."""
        return self._wake_datetime_localized(self._localize(reference))

    def next_wake_datetime(self, reference: datetime) -> datetime:
        """Return the next wake anchor strictly after *reference*."""
        reference = self._localize(reference)
        candidate = self._wake_datetime_localized(reference)
        if candidate > reference:
            return candidate
        return self._wake_datetime_localized(reference + timedelta(days=1))

    def sleep_datetime(self, reference: datetime) -> datetime:
        """Return the sleep anchor associated with the reference wake day."""
        reference = self._localize(reference)
        wake = self._wake_datetime_localized(reference)
        shift = self.weekend_sleep_shift if self._is_weekend(reference) else timedelta()
        candidate = datetime.combine(wake.date(), self.sleep, tzinfo=self.tzinfo) + shift
        if candidate <= wake:
            candidate += timedelta(days=1)
        return candidate

    def next_sleep_datetime(self, reference: datetime) -> datetime:
        """Return the next sleep anchor strictly after *reference*."""
        reference = self._localize(reference)
        candidate = self.sleep_datetime(reference)
        if candidate > reference:
            return candidate
        return self.sleep_datetime(reference + timedelta(days=1))

    def awake_block(self, reference: datetime) -> TimeBlock:
        """Return the awake window around the reference day."""
        reference = self._localize(reference)
        start = self._wake_datetime_localized(reference)
        shift = self.weekend_sleep_shift if self._is_weekend(reference) else timedelta()
        end = datetime.combine(start.date(), self.sleep, tzinfo=self.tzinfo) + shift
        if end <= start:
            end += timedelta(days=1)
        if end <= start:
            raise PersonaError("Persona sleep time resolved before wake time.")
        return TimeBlock(start=start, end=end, label="awake")

    def proportional_block(
        self,
        reference: datetime,
        start_fraction: float,
        end_fraction: float,
        label: str = "proportional",
    ) -> TimeBlock:
        """Return a fractional sub-block of the awake period."""
        if not (0.0 <= start_fraction <= 1.0 and 0.0 <= end_fraction <= 1.0):
            raise PersonaError("Fractions must be between 0 and 1.")

        if start_fraction > end_fraction:
            raise PersonaError("start_fraction must be <= end_fraction.")

        awake = self.awake_block(reference)
        duration = awake.duration

        return TimeBlock(
            start=awake.start + duration * start_fraction,
            end=awake.start + duration * end_fraction,
            label=label,
        )


@dataclass(frozen=True, slots=True)
class StandardWorker(PersonaProfile):
    """Typical daytime worker profile."""

    wake: clock_time = clock_time(6, 30)
    sleep: clock_time = clock_time(22, 30)


@dataclass(frozen=True, slots=True)
class NightShift(PersonaProfile):
    """Night-shift profile with a next-day sleep anchor."""

    wake: clock_time = clock_time(13, 0)
    sleep: clock_time = clock_time(5, 0)
