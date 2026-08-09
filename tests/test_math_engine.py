import pytest

from zeitwerkzeug.astro import math_engine as engine
from zeitwerkzeug.exceptions import SolarEventNotFoundError


def test_solar_noon_greenwich_is_near_midday_utc(
    greenwich,
    summer_solstice_2026,
) -> None:
    noon_utc = engine.solar_noon_utc_datetime(summer_solstice_2026, greenwich)
    assert noon_utc.hour in {11, 12, 13}


def test_sunrise_in_osaka_is_in_early_morning_local_time(
    osaka_japan,
    summer_solstice_2026,
) -> None:
    sunrise_utc = engine.event_utc_datetime(
        summer_solstice_2026,
        osaka_japan,
        altitude=-0.833,
        rising=True,
    )
    sunrise_local = sunrise_utc.astimezone(osaka_japan.tzinfo)
    assert 4 <= sunrise_local.hour <= 6


def test_polar_night_raises_for_sunrise(
    polar_location,
    winter_solstice_2026,
) -> None:
    with pytest.raises(SolarEventNotFoundError):
        engine.event_utc_datetime(
            winter_solstice_2026,
            polar_location,
            altitude=-0.833,
            rising=True,
        )
