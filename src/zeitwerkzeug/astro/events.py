"""Solar event definitions and custom solar angle targets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zeitwerkzeug.astro.constants import (
    ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG,
    CIVIL_TWILIGHT_ALTITUDE_DEG,
    GOLDEN_HOUR_ALTITUDE_DEG,
    HORIZON_APPARENT_ALTITUDE_DEG,
    NAUTICAL_TWILIGHT_ALTITUDE_DEG,
)


class SolarEvent(Enum):
    """Named solar events.

    Notes:
        - `DUSK` is civil dusk by default.
        - `GOLDEN_HOUR` is the morning start of golden hour at -4 degrees.
        - `SOLAR_NOON` is a transit event, not an altitude-crossing event.
    """

    SUNRISE = "sunrise"
    SUNSET = "sunset"

    CIVIL_DAWN = "civil_dawn"
    CIVIL_DUSK = "civil_dusk"

    NAUTICAL_DAWN = "nautical_dawn"
    NAUTICAL_DUSK = "nautical_dusk"

    ASTRONOMICAL_DAWN = "astronomical_dawn"
    ASTRONOMICAL_DUSK = "astronomical_dusk"

    GOLDEN_HOUR = "golden_hour"
    GOLDEN_HOUR_EVENING = "golden_hour_evening"

    DUSK = "dusk"
    SOLAR_NOON = "solar_noon"

    @property
    def altitude(self) -> float | None:
        """Solar altitude in degrees, if applicable."""
        if self is SolarEvent.SUNRISE:
            return HORIZON_APPARENT_ALTITUDE_DEG
        if self is SolarEvent.SUNSET:
            return HORIZON_APPARENT_ALTITUDE_DEG

        if self is SolarEvent.CIVIL_DAWN:
            return CIVIL_TWILIGHT_ALTITUDE_DEG
        if self is SolarEvent.CIVIL_DUSK:
            return CIVIL_TWILIGHT_ALTITUDE_DEG

        if self is SolarEvent.NAUTICAL_DAWN:
            return NAUTICAL_TWILIGHT_ALTITUDE_DEG
        if self is SolarEvent.NAUTICAL_DUSK:
            return NAUTICAL_TWILIGHT_ALTITUDE_DEG

        if self is SolarEvent.ASTRONOMICAL_DAWN:
            return ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG
        if self is SolarEvent.ASTRONOMICAL_DUSK:
            return ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG

        if self is SolarEvent.GOLDEN_HOUR:
            return GOLDEN_HOUR_ALTITUDE_DEG
        if self is SolarEvent.GOLDEN_HOUR_EVENING:
            return GOLDEN_HOUR_ALTITUDE_DEG

        if self is SolarEvent.DUSK:
            return CIVIL_TWILIGHT_ALTITUDE_DEG

        if self is SolarEvent.SOLAR_NOON:
            return None

        raise NotImplementedError(f"No altitude for {self!r}")

    @property
    def rising(self) -> bool | None:
        """Whether this event is on the rising or setting branch."""
        if self in {
            SolarEvent.SUNRISE,
            SolarEvent.CIVIL_DAWN,
            SolarEvent.NAUTICAL_DAWN,
            SolarEvent.ASTRONOMICAL_DAWN,
            SolarEvent.GOLDEN_HOUR,
        }:
            return True

        if self in {
            SolarEvent.SUNSET,
            SolarEvent.CIVIL_DUSK,
            SolarEvent.NAUTICAL_DUSK,
            SolarEvent.ASTRONOMICAL_DUSK,
            SolarEvent.GOLDEN_HOUR_EVENING,
            SolarEvent.DUSK,
        }:
            return False

        if self is SolarEvent.SOLAR_NOON:
            return None

        raise NotImplementedError(f"No rising/setting branch for {self!r}")


@dataclass(frozen=True, slots=True)
class SolarAngle:
    """Custom solar altitude target.

    Attributes:
        altitude: Target sun altitude in degrees.
        rising: True for the ascending branch, False for descending.
        name: Optional human-readable label.
    """

    altitude: float
    rising: bool = True
    name: str = "custom_solar_angle"

    @classmethod
    def from_event(cls, event: SolarEvent) -> SolarAngle:
        """Create a custom angle from a named solar event."""
        altitude = event.altitude
        rising = event.rising
        if altitude is None or rising is None:
            raise ValueError(f"Cannot convert {event!r} into a fixed solar angle.")
        return cls(altitude=altitude, rising=rising, name=event.value)


SolarTarget = SolarEvent | SolarAngle