"""Coverage and accuracy tests for zeitwerkzeug.astro.math_engine."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from zeitwerkzeug.astro import math_engine as engine
from zeitwerkzeug.astro.events import SolarAngle, SolarEvent
from zeitwerkzeug.astro.location import Location
from zeitwerkzeug.exceptions import SolarEventNotFoundError


@pytest.fixture(autouse=True)
def _clear_math_engine_caches():
    """
    The math engine uses lru_cache internally.

    Clear caches before and after each test so cache hits/misses and
    monkeypatched behavior remain deterministic.
    """
    engine._cached_event_minutes.cache_clear()
    engine._cached_transit_minutes.cache_clear()

    yield

    engine._cached_event_minutes.cache_clear()
    engine._cached_transit_minutes.cache_clear()


def _patch_hour_angle_math(monkeypatch, sin_value: float) -> None:
    """
    Patch math helpers used by _hour_angle_deg().

    This lets us deterministically exercise the numerical clamp branches
    without depending on fragile real-world floating point edge cases.
    """
    monkeypatch.setattr(engine.math, "radians", lambda value: value)
    monkeypatch.setattr(engine.math, "degrees", lambda value: value)
    monkeypatch.setattr(engine.math, "cos", lambda value: 1.0)

    def fake_sin(value: float) -> float:
        if value == 0.0:
            return 0.0
        return sin_value

    monkeypatch.setattr(engine.math, "sin", fake_sin)


# ---------------------------------------------------------------------------
# Basic datetime / degree helpers
# ---------------------------------------------------------------------------


def test_ensure_utc_naive_datetime_is_treated_as_utc() -> None:
    naive = datetime(2026, 6, 21, 12, 0)

    ensured = engine._ensure_utc(naive)

    assert ensured.tzinfo == UTC
    assert ensured == datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def test_ensure_utc_aware_datetime_is_converted_to_utc() -> None:
    tz_plus_2 = timezone(timedelta(hours=2))
    aware = datetime(2026, 6, 21, 14, 0, tzinfo=tz_plus_2)

    ensured = engine._ensure_utc(aware)

    assert ensured == datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def test_julian_day_known_epochs() -> None:
    unix_epoch = datetime(1970, 1, 1, 0, 0, tzinfo=UTC)
    j2000_epoch = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)

    assert engine.julian_day(unix_epoch) == pytest.approx(2440587.5, abs=1e-9)
    assert engine.julian_day(j2000_epoch) == pytest.approx(
        engine._J2000_JD,
        abs=1e-9,
    )


def test_julian_day_naive_datetime_is_treated_as_utc() -> None:
    naive = datetime(2000, 1, 1, 12, 0)
    aware = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)

    assert engine.julian_day(naive) == pytest.approx(engine.julian_day(aware), abs=1e-9)


def test_julian_day_non_utc_datetime_is_converted_to_utc() -> None:
    tz_plus_2 = timezone(timedelta(hours=2))
    aware_plus_2 = datetime(2000, 1, 1, 14, 0, tzinfo=tz_plus_2)
    aware_utc = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)

    assert engine.julian_day(aware_plus_2) == pytest.approx(
        engine.julian_day(aware_utc),
        abs=1e-9,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0.0),
        (360.0, 0.0),
        (361.0, 1.0),
        (-1.0, 359.0),
        (721.0, 1.0),
    ],
)
def test_normalize_degrees(value: float, expected: float) -> None:
    assert engine._normalize_degrees(value) == pytest.approx(expected, abs=1e-9)


def test_utc_midnight_returns_utc_midnight() -> None:
    assert engine._utc_midnight(date(2026, 6, 21)) == datetime(
        2026,
        6,
        21,
        0,
        0,
        tzinfo=UTC,
    )


# ---------------------------------------------------------------------------
# Solar geometry accuracy
# ---------------------------------------------------------------------------


def test_solar_geometry_summer_solstice_declination_is_near_positive_max() -> None:
    geom = engine.solar_geometry(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))

    assert geom.declination == pytest.approx(23.44, abs=0.5)
    assert geom.obliquity == pytest.approx(23.44, abs=0.5)
    assert abs(geom.equation_of_time_minutes) < 20.0
    assert math.isfinite(geom.true_longitude)


def test_solar_geometry_winter_solstice_declination_is_near_negative_max() -> None:
    geom = engine.solar_geometry(datetime(2026, 12, 21, 12, 0, tzinfo=UTC))

    assert geom.declination == pytest.approx(-23.44, abs=0.5)
    assert geom.obliquity == pytest.approx(23.44, abs=0.5)
    assert abs(geom.equation_of_time_minutes) < 20.0
    assert math.isfinite(geom.true_longitude)


# ---------------------------------------------------------------------------
# _hour_angle_deg edge cases and normal behavior
# ---------------------------------------------------------------------------


def test_hour_angle_denominator_zero_returns_zero_when_numerator_is_zero() -> None:
    # North pole, zero declination, zero altitude: degenerate but solvable as 0.
    assert engine._hour_angle_deg(90.0, 0.0, 0.0) == pytest.approx(0.0, abs=1e-9)


def test_hour_angle_denominator_zero_returns_none_when_numerator_nonzero() -> None:
    # North pole, zero declination, nonzero altitude: impossible/degenerate.
    assert engine._hour_angle_deg(90.0, 0.0, 10.0) is None


def test_hour_angle_clamps_small_positive_cos_h_to_zero(monkeypatch) -> None:
    _patch_hour_angle_math(monkeypatch, sin_value=1.0 + 1e-10)

    assert engine._hour_angle_deg(0.0, 0.0, 1.0) == 0.0


def test_hour_angle_clamps_small_negative_cos_h_to_180(monkeypatch) -> None:
    _patch_hour_angle_math(monkeypatch, sin_value=-1.0 - 1e-10)

    assert engine._hour_angle_deg(0.0, 0.0, 1.0) == 180.0


def test_hour_angle_returns_none_for_impossible_positive_cos_h(monkeypatch) -> None:
    _patch_hour_angle_math(monkeypatch, sin_value=2.0)

    assert engine._hour_angle_deg(0.0, 0.0, 1.0) is None


def test_hour_angle_returns_none_for_impossible_negative_cos_h(monkeypatch) -> None:
    _patch_hour_angle_math(monkeypatch, sin_value=-2.0)

    assert engine._hour_angle_deg(0.0, 0.0, 1.0) is None


def test_hour_angle_normal_value_is_between_0_and_180() -> None:
    angle = engine._hour_angle_deg(
        latitude=51.4779,
        declination=23.44,
        altitude=-0.833,
    )

    assert angle is not None
    assert 0.0 < angle < 180.0


# ---------------------------------------------------------------------------
# Low-level event/transit minute helpers
# ---------------------------------------------------------------------------


def test_event_minutes_greenwich_sunrise_is_reasonable(
    summer_solstice_2026,
    greenwich,
) -> None:
    lat_key, lon_key, _ = greenwich.key

    minutes = engine._event_minutes_on_utc_date(
        summer_solstice_2026,
        lat_key,
        lon_key,
        -0.833,
        True,
    )

    assert minutes is not None
    # Greenwich summer sunrise is roughly early UTC morning.
    assert 180.0 < minutes < 330.0


def test_event_minutes_greenwich_sunset_is_reasonable(
    summer_solstice_2026,
    greenwich,
) -> None:
    lat_key, lon_key, _ = greenwich.key

    minutes = engine._event_minutes_on_utc_date(
        summer_solstice_2026,
        lat_key,
        lon_key,
        -0.833,
        False,
    )

    assert minutes is not None
    # Greenwich summer sunset is roughly late UTC evening.
    assert 1140.0 < minutes < 1320.0


def test_event_minutes_returns_none_for_polar_night(
    winter_solstice_2026,
    polar_location,
) -> None:
    lat_key, lon_key, _ = polar_location.key

    minutes = engine._event_minutes_on_utc_date(
        winter_solstice_2026,
        lat_key,
        lon_key,
        -0.833,
        True,
    )

    assert minutes is None


def test_event_minutes_returns_none_when_result_not_finite(
    monkeypatch,
    summer_solstice_2026,
    greenwich,
) -> None:
    """
    Force the final finite-check branch in _event_minutes_on_utc_date().
    """
    monkeypatch.setattr(engine.math, "isfinite", lambda value: False)

    lat_key, lon_key, _ = greenwich.key

    minutes = engine._event_minutes_on_utc_date(
        summer_solstice_2026,
        lat_key,
        lon_key,
        -0.833,
        True,
    )

    assert minutes is None


def test_transit_minutes_greenwich_is_near_midday(
    summer_solstice_2026,
    greenwich,
) -> None:
    _, lon_key, _ = greenwich.key

    minutes = engine._transit_minutes_on_utc_date(
        summer_solstice_2026,
        lon_key,
    )

    # Greenwich is very close to UTC noon.
    assert 660.0 < minutes < 780.0


def test_transit_minutes_respects_longitude_shift() -> None:
    # 90 degrees east should have solar noon around 06:00 UTC.
    minutes = engine._transit_minutes_on_utc_date(
        date(2026, 6, 21),
        90.0,
    )

    assert 300.0 < minutes < 420.0


# ---------------------------------------------------------------------------
# Cached helper behavior
# ---------------------------------------------------------------------------


def test_cached_event_minutes_caches_results(
    summer_solstice_2026,
    greenwich,
) -> None:
    lat_key, lon_key, _ = greenwich.key
    args = (summer_solstice_2026, lat_key, lon_key, -0.833, True)

    first = engine._cached_event_minutes(*args)
    second = engine._cached_event_minutes(*args)

    assert first == second

    info = engine._cached_event_minutes.cache_info()
    assert info.hits >= 1
    assert info.misses >= 1


def test_cached_transit_minutes_caches_results(
    summer_solstice_2026,
    greenwich,
) -> None:
    _, lon_key, _ = greenwich.key
    args = (summer_solstice_2026, lon_key)

    first = engine._cached_transit_minutes(*args)
    second = engine._cached_transit_minutes(*args)

    assert first == second

    info = engine._cached_transit_minutes.cache_info()
    assert info.hits >= 1
    assert info.misses >= 1


# ---------------------------------------------------------------------------
# event_utc_datetime accuracy and edge behavior
# ---------------------------------------------------------------------------


def test_event_utc_datetime_greenwich_sunrise_is_accurate(
    summer_solstice_2026,
    greenwich,
) -> None:
    sunrise_utc = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE.altitude,
        SolarEvent.SUNRISE.rising,
    )

    assert sunrise_utc.tzinfo == UTC
    assert sunrise_utc.date() == summer_solstice_2026

    # Tighter than "hour in {11, 12, 13}" style checks.
    assert datetime(2026, 6, 21, 3, 0, tzinfo=UTC) < sunrise_utc
    assert sunrise_utc < datetime(2026, 6, 21, 5, 0, tzinfo=UTC)


def test_event_utc_datetime_greenwich_sunset_is_accurate(
    summer_solstice_2026,
    greenwich,
) -> None:
    sunset_utc = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNSET.altitude,
        SolarEvent.SUNSET.rising,
    )

    assert sunset_utc.tzinfo == UTC
    assert sunset_utc.date() == summer_solstice_2026

    assert datetime(2026, 6, 21, 19, 0, tzinfo=UTC) < sunset_utc
    assert sunset_utc < datetime(2026, 6, 21, 22, 0, tzinfo=UTC)


def test_event_utc_datetime_osaka_sunrise_local_is_accurate(
    summer_solstice_2026,
    osaka_japan,
) -> None:
    sunrise_utc = engine.event_utc_datetime(
        summer_solstice_2026,
        osaka_japan,
        SolarEvent.SUNRISE.altitude,
        SolarEvent.SUNRISE.rising,
    )

    sunrise_local = sunrise_utc.astimezone(osaka_japan.tzinfo)

    assert sunrise_local.date() == summer_solstice_2026
    assert time(4, 0) <= sunrise_local.time() <= time(5, 30)


def test_event_utc_datetime_daily_ordering_is_correct(
    summer_solstice_2026,
    greenwich,
) -> None:
    civil_dawn = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.CIVIL_DAWN,
    )
    sunrise = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE,
    )
    noon = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SOLAR_NOON,
    )
    sunset = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNSET,
    )
    civil_dusk = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.CIVIL_DUSK,
    )

    assert civil_dawn < sunrise < noon < sunset < civil_dusk


def test_event_utc_datetime_raises_for_impossible_altitude(
    summer_solstice_2026,
    greenwich,
) -> None:
    with pytest.raises(SolarEventNotFoundError):
        engine.event_utc_datetime(
            summer_solstice_2026,
            greenwich,
            altitude=90.0,
            rising=True,
        )


def test_event_utc_datetime_raises_for_polar_night(
    winter_solstice_2026,
    polar_location,
) -> None:
    with pytest.raises(SolarEventNotFoundError):
        engine.event_utc_datetime(
            winter_solstice_2026,
            polar_location,
            altitude=-0.833,
            rising=True,
        )


def test_event_utc_datetime_raises_for_midnight_sun(
    summer_solstice_2026,
    polar_location,
) -> None:
    with pytest.raises(SolarEventNotFoundError):
        engine.event_utc_datetime(
            summer_solstice_2026,
            polar_location,
            altitude=-0.833,
            rising=True,
        )


def test_event_utc_datetime_skips_none_minutes(
    monkeypatch,
    summer_solstice_2026,
    greenwich,
) -> None:
    monkeypatch.setattr(engine, "_cached_event_minutes", lambda *args, **kwargs: None)

    with pytest.raises(SolarEventNotFoundError):
        engine.event_utc_datetime(
            summer_solstice_2026,
            greenwich,
            altitude=-0.833,
            rising=True,
        )


def test_event_utc_datetime_skips_non_finite_minutes(
    monkeypatch,
    summer_solstice_2026,
    greenwich,
) -> None:
    monkeypatch.setattr(
        engine,
        "_cached_event_minutes",
        lambda *args, **kwargs: float("inf"),
    )

    with pytest.raises(SolarEventNotFoundError):
        engine.event_utc_datetime(
            summer_solstice_2026,
            greenwich,
            altitude=-0.833,
            rising=True,
        )


# ---------------------------------------------------------------------------
# solar_noon_utc_datetime accuracy and edge behavior
# ---------------------------------------------------------------------------


def test_solar_noon_greenwich_is_accurate(
    summer_solstice_2026,
    greenwich,
) -> None:
    noon_utc = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)

    assert noon_utc.tzinfo == UTC
    assert noon_utc.date() == summer_solstice_2026

    # Much tighter than the original hour-in-{11, 12, 13} check.
    expected_midday = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert abs(noon_utc - expected_midday) < timedelta(minutes=30)


def test_solar_noon_osaka_local_is_near_local_midday(
    summer_solstice_2026,
    osaka_japan,
) -> None:
    noon_utc = engine.solar_noon_utc_datetime(summer_solstice_2026, osaka_japan)
    noon_local = noon_utc.astimezone(osaka_japan.tzinfo)

    assert noon_local.date() == summer_solstice_2026
    assert time(11, 30) <= noon_local.time() <= time(12, 30)


def test_solar_noon_respects_longitude() -> None:
    location = Location(lat=0.0, lon=90.0, timezone="UTC")

    noon_utc = engine.solar_noon_utc_datetime(date(2026, 6, 21), location)

    # 90E should have solar noon near 06:00 UTC.
    assert datetime(2026, 6, 21, 5, 30, tzinfo=UTC) < noon_utc
    assert noon_utc < datetime(2026, 6, 21, 6, 30, tzinfo=UTC)


def test_solar_noon_raises_when_no_candidate_is_in_local_day(
    monkeypatch,
    summer_solstice_2026,
    greenwich,
) -> None:
    # Force every candidate transit outside the local-day window.
    monkeypatch.setattr(
        engine,
        "_cached_transit_minutes",
        lambda *args, **kwargs: -100_000.0,
    )

    with pytest.raises(SolarEventNotFoundError):
        engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)


# ---------------------------------------------------------------------------
# event_from_target_utc
# ---------------------------------------------------------------------------


def test_event_from_target_utc_solar_noon_matches_direct_noon(
    summer_solstice_2026,
    greenwich,
) -> None:
    resolved = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SOLAR_NOON,
    )
    expected = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)

    assert resolved == expected


def test_event_from_target_utc_sunrise_matches_direct_event(
    summer_solstice_2026,
    greenwich,
) -> None:
    resolved = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE,
    )
    expected = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE.altitude,
        SolarEvent.SUNRISE.rising,
    )

    assert resolved == expected


def test_event_from_target_utc_solar_angle_matches_direct_event(
    summer_solstice_2026,
    greenwich,
) -> None:
    angle = SolarAngle.from_event(SolarEvent.CIVIL_DAWN)

    resolved = engine.event_from_target_utc(
        summer_solstice_2026,
        greenwich,
        angle,
    )
    expected = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        angle.altitude,
        angle.rising,
    )

    assert resolved == expected


def test_event_from_target_utc_raises_for_unresolvable_solar_event(
    monkeypatch,
    summer_solstice_2026,
    greenwich,
) -> None:
    """
    Cover the defensive branch where a SolarEvent-like object has no
    altitude/rising mapping.
    """

    class FakeSolarEvent:
        SOLAR_NOON = object()

        def __init__(self, altitude: float | None, rising: bool | None) -> None:
            self.altitude = altitude
            self.rising = rising

    monkeypatch.setattr(engine, "SolarEvent", FakeSolarEvent)

    target = FakeSolarEvent(None, None)

    with pytest.raises(SolarEventNotFoundError):
        engine.event_from_target_utc(summer_solstice_2026, greenwich, target)


def test_event_from_target_utc_raises_type_error_for_unsupported_target(
    summer_solstice_2026,
    greenwich,
) -> None:
    with pytest.raises(TypeError):
        engine.event_from_target_utc(
            summer_solstice_2026,
            greenwich,
            object(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# next_solar_event_utc
# ---------------------------------------------------------------------------


def test_next_solar_event_utc_returns_same_day_event_when_before_it(
    summer_solstice_2026,
    greenwich,
) -> None:
    after = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)

    resolved = engine.next_solar_event_utc(after, greenwich, SolarEvent.SUNRISE)
    expected = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE.altitude,
        SolarEvent.SUNRISE.rising,
    )

    assert resolved == expected


def test_next_solar_event_utc_rolls_to_next_day_when_after_event(
    summer_solstice_2026,
    greenwich,
) -> None:
    sunrise = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE.altitude,
        SolarEvent.SUNRISE.rising,
    )

    resolved = engine.next_solar_event_utc(sunrise, greenwich, SolarEvent.SUNRISE)

    assert resolved > sunrise
    assert resolved.date() == sunrise.date() + timedelta(days=1)


def test_next_solar_event_utc_solar_noon_rolls_to_next_day_when_at_exact_noon(
    summer_solstice_2026,
    greenwich,
) -> None:
    noon = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)

    resolved = engine.next_solar_event_utc(noon, greenwich, SolarEvent.SOLAR_NOON)

    assert resolved > noon
    assert resolved.date() == noon.date() + timedelta(days=1)


def test_next_solar_event_utc_accepts_naive_after_as_utc(
    summer_solstice_2026,
    greenwich,
) -> None:
    after_naive = datetime(2026, 6, 21, 0, 0)

    resolved = engine.next_solar_event_utc(after_naive, greenwich, SolarEvent.SUNRISE)

    assert resolved.tzinfo == UTC
    assert resolved.date() == summer_solstice_2026


def test_next_solar_event_utc_converts_non_utc_after(
    summer_solstice_2026,
    osaka_japan,
) -> None:
    after_local = datetime(2026, 6, 21, 0, 0, tzinfo=osaka_japan.tzinfo)

    resolved = engine.next_solar_event_utc(
        after_local,
        osaka_japan,
        SolarEvent.SUNRISE,
    )

    assert resolved.tzinfo == UTC
    assert resolved > after_local.astimezone(UTC)


def test_next_solar_event_utc_with_solar_angle(
    summer_solstice_2026,
    greenwich,
) -> None:
    after = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)
    angle = SolarAngle.from_event(SolarEvent.CIVIL_DAWN)

    resolved = engine.next_solar_event_utc(after, greenwich, angle)
    expected = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        angle.altitude,
        angle.rising,
    )

    assert resolved == expected


def test_next_solar_event_utc_raises_when_no_future_occurrence_within_search_window(
    greenwich,
) -> None:
    after = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)

    # 90 degrees altitude is impossible for Greenwich.
    impossible = SolarAngle(altitude=90.0, rising=True)

    with pytest.raises(SolarEventNotFoundError):
        engine.next_solar_event_utc(
            after,
            greenwich,
            impossible,
            search_days=1,
        )


def test_next_solar_event_utc_raises_for_polar_night_within_short_search_window(
    winter_solstice_2026,
    polar_location,
) -> None:
    after = datetime(2026, 12, 21, 0, 0, tzinfo=UTC)

    with pytest.raises(SolarEventNotFoundError):
        engine.next_solar_event_utc(
            after,
            polar_location,
            SolarEvent.SUNRISE,
            search_days=1,
        )


# ---------------------------------------------------------------------------
# sun_altitude accuracy
# ---------------------------------------------------------------------------


def test_sun_altitude_at_solar_noon_is_high(
    summer_solstice_2026,
    greenwich,
) -> None:
    noon_utc = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)

    altitude = engine.sun_altitude(noon_utc, greenwich)

    # Greenwich latitude ~51.48, declination ~+23.44 -> altitude ~62 degrees.
    assert altitude == pytest.approx(62.0, abs=5.0)


def test_sun_altitude_at_midnight_is_negative(
    summer_solstice_2026,
    greenwich,
) -> None:
    midnight_utc = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)

    altitude = engine.sun_altitude(midnight_utc, greenwich)

    assert altitude < 0.0


def test_sun_altitude_accepts_naive_datetime_as_utc(greenwich) -> None:
    altitude = engine.sun_altitude(datetime(2026, 6, 21, 12, 0), greenwich)

    assert math.isfinite(altitude)


def test_sun_altitude_converts_non_utc_datetime(
    summer_solstice_2026,
    greenwich,
) -> None:
    noon_utc = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)

    tz_plus_2 = timezone(timedelta(hours=2))
    noon_plus_2 = noon_utc.astimezone(tz_plus_2)

    altitude_utc = engine.sun_altitude(noon_utc, greenwich)
    altitude_other_tz = engine.sun_altitude(noon_plus_2, greenwich)

    assert altitude_other_tz == pytest.approx(altitude_utc, abs=1e-6)


def test_sun_altitude_noon_is_greater_than_sunrise(
    summer_solstice_2026,
    greenwich,
) -> None:
    sunrise = engine.event_utc_datetime(
        summer_solstice_2026,
        greenwich,
        SolarEvent.SUNRISE.altitude,
        SolarEvent.SUNRISE.rising,
    )
    noon = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)

    sunrise_altitude = engine.sun_altitude(sunrise, greenwich)
    noon_altitude = engine.sun_altitude(noon, greenwich)

    assert noon_altitude > sunrise_altitude


def test_sun_altitude_near_zenith_at_equator_equinox() -> None:
    location = Location(lat=0.0, lon=0.0, timezone="UTC")
    local_date = date(2026, 3, 20)

    noon_utc = engine.solar_noon_utc_datetime(local_date, location)
    altitude = engine.sun_altitude(noon_utc, location)

    assert altitude > 80.0
