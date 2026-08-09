from datetime import UTC, datetime


def test_time_target_resolves_same_day(utc_morning, utc_noon_trigger) -> None:
    resolved = utc_noon_trigger.resolve_after(utc_morning)
    assert resolved == datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def test_time_target_rolls_to_next_day(utc_afternoon, utc_noon_trigger) -> None:
    resolved = utc_noon_trigger.resolve_after(utc_afternoon)
    assert resolved == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_require_returns_new_schedule_instance(utc_noon_trigger, true_condition) -> None:
    with_condition = utc_noon_trigger.require(true_condition)
    assert utc_noon_trigger.conditions == ()
    assert len(with_condition.conditions) == 1
