"""Additional coverage tests for zeitwerkzeug.context.scheduler."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from zeitwerkzeug import schedule
from zeitwerkzeug.exceptions import ScheduleError

try:
    scheduler_mod = importlib.import_module(type(schedule).__module__)
except ImportError:
    scheduler_mod = importlib.import_module("zeitwerkzeug.context.scheduler")


FailPolicy = scheduler_mod.FailPolicy
LazySchedule = scheduler_mod.LazySchedule
_as_utc = scheduler_mod._as_utc
_next_time_after = scheduler_mod._next_time_after


class DummySolarEvent:
    """Dummy stand-in for SolarEvent so solar branches can be tested deterministically."""


class DummySolarAngle:
    """Dummy stand-in for SolarAngle so solar branches can be tested deterministically."""


class DummyCondition:
    """Minimal condition plugin double."""

    def evaluate(self, context) -> bool:
        return True


@pytest.fixture
def dummy_solar_types(monkeypatch):
    """
    Patch SolarEvent/SolarAngle inside the scheduler module.

    This allows coverage of solar branches without depending on the real
    astronomical types or next_solar_event_utc implementation.
    """
    monkeypatch.setattr(scheduler_mod, "SolarEvent", DummySolarEvent)
    monkeypatch.setattr(scheduler_mod, "SolarAngle", DummySolarAngle)
    return DummySolarEvent, DummySolarAngle


@pytest.fixture
def fake_next_solar_event(monkeypatch):
    """
    Patch next_solar_event_utc with a deterministic fake.

    Returns the call recorder list so tests can assert on the arguments.
    """
    calls: list[tuple[datetime, object, object]] = []

    def _fake_next_solar_event_utc(
        after_utc: datetime,
        location: object,
        target: object,
    ) -> datetime:
        calls.append((after_utc, location, target))
        return datetime(2026, 8, 9, 18, 0, tzinfo=UTC)

    monkeypatch.setattr(scheduler_mod, "next_solar_event_utc", _fake_next_solar_event_utc)
    return calls


# ---------------------------------------------------------------------------
# _as_utc()
# ---------------------------------------------------------------------------


def test_as_utc_naive_datetime_uses_default_timezone() -> None:
    naive = datetime(2026, 8, 9, 12, 0)
    tokyo = ZoneInfo("Asia/Tokyo")

    assert _as_utc(naive, tokyo) == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_as_utc_naive_datetime_defaults_to_utc() -> None:
    naive = datetime(2026, 8, 9, 12, 0)

    assert _as_utc(naive, UTC) == datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def test_as_utc_aware_datetime_converts_to_utc() -> None:
    tokyo = ZoneInfo("Asia/Tokyo")
    aware = datetime(2026, 8, 9, 12, 0, tzinfo=tokyo)

    assert _as_utc(aware, UTC) == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _next_time_after()
# ---------------------------------------------------------------------------


def test_next_time_after_same_day_in_non_utc_timezone() -> None:
    # 2026-08-09 00:00 UTC == 2026-08-09 09:00 Asia/Tokyo.
    after_utc = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)

    resolved = _next_time_after(after_utc, time(12, 0), ZoneInfo("Asia/Tokyo"))

    # 2026-08-09 12:00 Asia/Tokyo == 2026-08-09 03:00 UTC.
    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_next_time_after_rolls_to_next_day_in_non_utc_timezone() -> None:
    # 2026-08-09 05:00 UTC == 2026-08-09 14:00 Asia/Tokyo.
    after_utc = datetime(2026, 8, 9, 5, 0, tzinfo=UTC)

    resolved = _next_time_after(after_utc, time(12, 0), ZoneInfo("Asia/Tokyo"))

    # Next occurrence is 2026-08-10 12:00 Asia/Tokyo == 2026-08-10 03:00 UTC.
    assert resolved == datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def test_next_time_after_exact_match_rolls_to_next_day() -> None:
    after_utc = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    resolved = _next_time_after(after_utc, time(12, 0), UTC)

    assert resolved == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# FailPolicy construction and resolve_limit()
# ---------------------------------------------------------------------------


def test_fail_policy_defaults() -> None:
    policy = FailPolicy()

    assert policy.retry_interval == timedelta(minutes=10)
    assert policy.limit is None
    assert policy.max_attempts is None


def test_fail_policy_resolve_limit_none(utc_noon_trigger) -> None:
    policy = FailPolicy()
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    assert policy.resolve_limit(after, utc_noon_trigger) is None


def test_fail_policy_resolve_limit_timedelta(utc_noon_trigger) -> None:
    policy = FailPolicy(limit=timedelta(minutes=30))
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, utc_noon_trigger)

    assert resolved == datetime(2026, 8, 9, 12, 30, tzinfo=UTC)


def test_fail_policy_resolve_limit_aware_datetime_converts_to_utc(
    utc_noon_trigger,
) -> None:
    # 15:00 Asia/Tokyo == 06:00 UTC.
    limit = datetime(2026, 8, 9, 15, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    policy = FailPolicy(limit=limit)
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, utc_noon_trigger)

    assert resolved == datetime(2026, 8, 9, 6, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_naive_datetime_defaults_to_utc() -> None:
    policy = FailPolicy(limit=datetime(2026, 8, 9, 15, 0))
    lazy = schedule.at(time(0, 0))
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    assert resolved == datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_naive_datetime_uses_schedule_timezone() -> None:
    policy = FailPolicy(limit=datetime(2026, 8, 9, 12, 0))
    lazy = schedule.at(time(0, 0), tz="Asia/Tokyo")
    after = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    # 12:00 Asia/Tokyo == 03:00 UTC.
    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_naive_datetime_prefers_location_timezone(
    osaka_japan,
) -> None:
    policy = FailPolicy(limit=datetime(2026, 8, 9, 12, 0))
    lazy = schedule.at(time(0, 0), location=osaka_japan, tz="UTC")
    after = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    # Location timezone should win over tz="UTC".
    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_clock_time_defaults_to_utc() -> None:
    policy = FailPolicy(limit=time(15, 0))
    lazy = schedule.at(time(0, 0))
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    assert resolved == datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_clock_time_same_day() -> None:
    policy = FailPolicy(limit=time(12, 0))
    lazy = schedule.at(time(0, 0), tz="Asia/Tokyo")
    after = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    # 2026-08-09 12:00 Asia/Tokyo == 2026-08-09 03:00 UTC.
    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_clock_time_rolls_to_next_day() -> None:
    policy = FailPolicy(limit=time(12, 0))
    lazy = schedule.at(time(0, 0), tz="Asia/Tokyo")
    after = datetime(2026, 8, 9, 5, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    # After 14:00 Asia/Tokyo, next 12:00 is the following day.
    assert resolved == datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_clock_time_uses_location_timezone(
    osaka_japan,
) -> None:
    policy = FailPolicy(limit=time(12, 0))
    lazy = schedule.at(time(0, 0), location=osaka_japan, tz="UTC")
    after = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)

    resolved = policy.resolve_limit(after, lazy)

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_fail_policy_resolve_limit_solar_requires_location(dummy_solar_types) -> None:
    SolarEvent, SolarAngle = dummy_solar_types
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    for solar_type in (SolarEvent, SolarAngle):
        policy = FailPolicy(limit=solar_type())
        lazy = schedule.at(time(12, 0), tz="UTC")

        with pytest.raises(ScheduleError, match="location"):
            policy.resolve_limit(after, lazy)


def test_fail_policy_resolve_limit_solar_with_location(
    dummy_solar_types,
    fake_next_solar_event,
    greenwich,
) -> None:
    SolarEvent, SolarAngle = dummy_solar_types
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    for solar_type in (SolarEvent, SolarAngle):
        fake_next_solar_event.clear()

        limit = solar_type()
        policy = FailPolicy(limit=limit)
        lazy = schedule.at(time(12, 0), location=greenwich)

        resolved = policy.resolve_limit(after, lazy)

        assert resolved == datetime(2026, 8, 9, 18, 0, tzinfo=UTC)
        assert fake_next_solar_event == [(after, greenwich, limit)]


def test_fail_policy_resolve_limit_unsupported_target(utc_noon_trigger) -> None:
    policy = FailPolicy(limit=object())
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    with pytest.raises(ScheduleError, match="Unsupported limit target"):
        policy.resolve_limit(after, utc_noon_trigger)


# ---------------------------------------------------------------------------
# LazySchedule.timezone_info
# ---------------------------------------------------------------------------


def test_timezone_info_defaults_to_utc() -> None:
    lazy = schedule.at(time(12, 0))

    assert lazy.timezone_info is UTC
    assert lazy.timezone_info.utcoffset(datetime(2026, 8, 9)) == timedelta(0)


def test_timezone_info_uses_named_timezone() -> None:
    lazy = schedule.at(time(12, 0), tz="Asia/Tokyo")

    assert lazy.timezone_info.utcoffset(datetime(2026, 8, 9)) == timedelta(hours=9)


def test_timezone_info_prefers_location_over_tz(osaka_japan) -> None:
    lazy = schedule.at(time(12, 0), location=osaka_japan, tz="UTC")

    # Location should win over explicit tz.
    assert lazy.timezone_info.utcoffset(datetime(2026, 8, 9)) == timedelta(hours=9)


# ---------------------------------------------------------------------------
# LazySchedule.require()
# ---------------------------------------------------------------------------


def test_require_appends_multiple_conditions_in_order(utc_noon_trigger, true_condition) -> None:
    other = DummyCondition()

    lazy = utc_noon_trigger.require(true_condition, other)

    assert utc_noon_trigger.conditions == ()
    assert lazy.conditions == (true_condition, other)


def test_require_chains_without_mutating_original(utc_noon_trigger, true_condition) -> None:
    first = utc_noon_trigger.require(true_condition)
    second = first.require(DummyCondition())

    assert utc_noon_trigger.conditions == ()
    assert len(first.conditions) == 1
    assert len(second.conditions) == 2


def test_require_rejects_object_without_evaluate(utc_noon_trigger) -> None:
    with pytest.raises(TypeError, match="evaluate"):
        utc_noon_trigger.require(object())


# ---------------------------------------------------------------------------
# LazySchedule.on_fail()
# ---------------------------------------------------------------------------


def test_on_fail_returns_new_schedule_with_default_policy(utc_noon_trigger) -> None:
    lazy = utc_noon_trigger.on_fail()

    assert utc_noon_trigger.fail_policy is None
    assert lazy is not utc_noon_trigger

    policy = lazy.fail_policy
    assert policy is not None
    assert policy.retry_interval == timedelta(minutes=10)
    assert policy.limit is None
    assert policy.max_attempts is None


def test_on_fail_accepts_retry_interval_minutes(utc_noon_trigger) -> None:
    lazy = utc_noon_trigger.on_fail(retry_interval_mins=5, max_attempts=2)

    policy = lazy.fail_policy
    assert policy is not None
    assert policy.retry_interval == timedelta(minutes=5)
    assert policy.max_attempts == 2


def test_on_fail_accepts_fractional_retry_interval_minutes(utc_noon_trigger) -> None:
    lazy = utc_noon_trigger.on_fail(retry_interval_mins=0.5)

    policy = lazy.fail_policy
    assert policy is not None
    assert policy.retry_interval == timedelta(seconds=30)


def test_on_fail_prefers_explicit_retry_interval_over_minutes(utc_noon_trigger) -> None:
    explicit = timedelta(seconds=90)

    lazy = utc_noon_trigger.on_fail(
        retry_interval_mins=1,
        retry_interval=explicit,
    )

    policy = lazy.fail_policy
    assert policy is not None
    assert policy.retry_interval == explicit


def test_on_fail_stores_limit_and_max_attempts(utc_noon_trigger) -> None:
    limit = time(12, 0)

    lazy = utc_noon_trigger.on_fail(limit=limit, max_attempts=7)

    policy = lazy.fail_policy
    assert policy is not None
    assert policy.limit == limit
    assert policy.max_attempts == 7


# ---------------------------------------------------------------------------
# LazySchedule.resolve_after(): datetime targets
# ---------------------------------------------------------------------------


def test_resolve_after_accepts_naive_after_as_utc() -> None:
    target = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    lazy = schedule.at(target)

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 12, 0))

    assert resolved == target


def test_resolve_after_future_aware_datetime_returns_utc() -> None:
    target = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    lazy = schedule.at(target)

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))

    assert resolved == target


def test_resolve_after_future_aware_datetime_converts_to_utc() -> None:
    # 12:00 Asia/Tokyo == 03:00 UTC.
    target = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    lazy = schedule.at(target)

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_resolve_after_future_naive_datetime_defaults_to_utc() -> None:
    target = datetime(2026, 8, 9, 15, 0)
    lazy = schedule.at(target)

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_resolve_after_future_naive_datetime_uses_schedule_timezone() -> None:
    target = datetime(2026, 8, 9, 12, 0)
    lazy = schedule.at(target, tz="Asia/Tokyo")

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_resolve_after_future_naive_datetime_prefers_location_timezone(
    osaka_japan,
) -> None:
    target = datetime(2026, 8, 9, 12, 0)
    lazy = schedule.at(target, location=osaka_japan, tz="UTC")

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_resolve_after_past_datetime_raises() -> None:
    target = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)
    lazy = schedule.at(target)

    with pytest.raises(ScheduleError, match="past"):
        lazy.resolve_after(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))


def test_resolve_after_equal_datetime_raises() -> None:
    target = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    lazy = schedule.at(target)

    with pytest.raises(ScheduleError):
        lazy.resolve_after(target)


def test_resolve_after_naive_past_datetime_uses_schedule_timezone() -> None:
    # 12:00 Asia/Tokyo == 03:00 UTC.
    target = datetime(2026, 8, 9, 12, 0)
    lazy = schedule.at(target, tz="Asia/Tokyo")

    # 05:00 UTC == 14:00 Asia/Tokyo, so the target is already past.
    with pytest.raises(ScheduleError):
        lazy.resolve_after(datetime(2026, 8, 9, 5, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# LazySchedule.resolve_after(): clock_time targets
# ---------------------------------------------------------------------------


def test_resolve_after_clock_target_defaults_to_utc() -> None:
    lazy = schedule.at(time(15, 0))

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_resolve_after_clock_target_with_named_timezone_same_day() -> None:
    lazy = schedule.at(time(12, 0), tz="Asia/Tokyo")

    # 00:00 UTC == 09:00 Asia/Tokyo, so 12:00 local is still ahead.
    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_resolve_after_clock_target_with_named_timezone_rolls_to_next_day() -> None:
    lazy = schedule.at(time(12, 0), tz="Asia/Tokyo")

    # 05:00 UTC == 14:00 Asia/Tokyo, so 12:00 local must roll to next day.
    resolved = lazy.resolve_after(datetime(2026, 8, 9, 5, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def test_resolve_after_clock_target_with_location(osaka_japan) -> None:
    lazy = schedule.at(time(12, 0), location=osaka_japan)

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# LazySchedule.resolve_after(): callable targets
# ---------------------------------------------------------------------------


def test_resolve_after_callable_target_receives_utc_after() -> None:
    seen: list[datetime] = []

    def target(after: datetime) -> datetime:
        seen.append(after)
        return after + timedelta(hours=2)

    lazy = schedule.at(target)
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    resolved = lazy.resolve_after(after)

    assert resolved == datetime(2026, 8, 9, 14, 0, tzinfo=UTC)
    assert seen == [after]


def test_resolve_after_callable_target_receives_utc_when_after_is_naive() -> None:
    seen: list[datetime] = []

    def target(after: datetime) -> datetime:
        seen.append(after)
        return after + timedelta(hours=1)

    lazy = schedule.at(target)

    lazy.resolve_after(datetime(2026, 8, 9, 12, 0))

    assert seen == [datetime(2026, 8, 9, 12, 0, tzinfo=UTC)]


def test_resolve_after_callable_naive_result_defaults_to_utc() -> None:
    lazy = schedule.at(lambda after: datetime(2026, 8, 9, 15, 0))

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def test_resolve_after_callable_naive_result_uses_schedule_timezone() -> None:
    lazy = schedule.at(
        lambda after: datetime(2026, 8, 9, 12, 0),
        tz="Asia/Tokyo",
    )

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_resolve_after_callable_naive_result_prefers_location_timezone(
    osaka_japan,
) -> None:
    lazy = schedule.at(
        lambda after: datetime(2026, 8, 9, 12, 0),
        location=osaka_japan,
        tz="UTC",
    )

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_resolve_after_callable_aware_result_converts_to_utc() -> None:
    lazy = schedule.at(
        lambda after: datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        tz="UTC",
    )

    resolved = lazy.resolve_after(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))

    assert resolved == datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# LazySchedule.resolve_after(): solar targets
# ---------------------------------------------------------------------------


def test_resolve_after_solar_target_requires_location(dummy_solar_types) -> None:
    SolarEvent, SolarAngle = dummy_solar_types
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    for solar_type in (SolarEvent, SolarAngle):
        lazy = schedule.at(solar_type())

        with pytest.raises(ScheduleError, match="location"):
            lazy.resolve_after(after)


def test_resolve_after_solar_target_with_location(
    dummy_solar_types,
    fake_next_solar_event,
    greenwich,
) -> None:
    SolarEvent, SolarAngle = dummy_solar_types
    after = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    for solar_type in (SolarEvent, SolarAngle):
        fake_next_solar_event.clear()

        target = solar_type()
        lazy = schedule.at(target, location=greenwich)

        resolved = lazy.resolve_after(after)

        assert resolved == datetime(2026, 8, 9, 18, 0, tzinfo=UTC)
        assert fake_next_solar_event == [(after, greenwich, target)]


# ---------------------------------------------------------------------------
# LazySchedule.resolve_after(): unsupported targets
# ---------------------------------------------------------------------------


def test_resolve_after_unsupported_target_raises() -> None:
    lazy = schedule.at(123)  # type: ignore[arg-type]

    with pytest.raises(ScheduleError, match="Unsupported schedule target"):
        lazy.resolve_after(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# ScheduleBuilder / LazySchedule construction
# ---------------------------------------------------------------------------


def test_schedule_builder_sets_core_fields(greenwich) -> None:
    target = time(12, 0)

    lazy = schedule.at(target, location=greenwich, tz="UTC")

    assert isinstance(lazy, LazySchedule)
    assert lazy.target == target
    assert lazy.location is greenwich
    assert lazy.tz == "UTC"
    assert lazy.conditions == ()
    assert lazy.fail_policy is None
    assert lazy.metadata == {}


def test_lazy_schedule_default_metadata_is_empty() -> None:
    lazy = LazySchedule(target=time(12, 0))

    assert lazy.metadata == {}


def test_lazy_schedule_accepts_metadata() -> None:
    lazy = LazySchedule(target=time(12, 0), metadata={"team": "core"})

    assert lazy.metadata == {"team": "core"}
