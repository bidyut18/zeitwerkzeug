"""Solar event definitions and custom solar angle targets.

This module provides:
- `SolarEvent`: Named solar events (sunrise, sunset, twilights, golden hour, solar noon)
- `SolarAngle`: Custom solar altitude targets with rising/setting branch
- `SolarTarget`: Union type for either named events or custom angles
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zeitwerkzeug.astro.constants import (
    ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG,
    CIVIL_TWILIGHT_ALTITUDE_DEG,
    GOLDEN_HOUR_ALTITUDE_DEG,
    HORIZON_APPARENT_ALTITUDE_DEG,
    ISNA_DAWN_ALTITUDE_DEG,
    NAUTICAL_TWILIGHT_ALTITUDE_DEG,
)

# Internal mapping of event -> (altitude, rising)
# Using a dict avoids repetitive if/elif chains in properties.
# SOLAR_NOON has altitude=None, rising=None (transit event, not altitude crossing)
_EVENT_DATA: dict[str, tuple[float | None, bool | None]] = {
    # Standard sunrise/sunset (apparent horizon with refraction)
    "SUNRISE": (HORIZON_APPARENT_ALTITUDE_DEG, True),
    "SUNSET": (HORIZON_APPARENT_ALTITUDE_DEG, False),
    # Civil twilight (-6°)
    "CIVIL_DAWN": (CIVIL_TWILIGHT_ALTITUDE_DEG, True),
    "CIVIL_DUSK": (CIVIL_TWILIGHT_ALTITUDE_DEG, False),
    # Nautical twilight (-12°)
    "NAUTICAL_DAWN": (NAUTICAL_TWILIGHT_ALTITUDE_DEG, True),
    "NAUTICAL_DUSK": (NAUTICAL_TWILIGHT_ALTITUDE_DEG, False),
    # Astronomical twilight (-18°)
    "ASTRONOMICAL_DAWN": (ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG, True),
    "ASTRONOMICAL_DUSK": (ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG, False),
    # Golden hour (-4°)
    "GOLDEN_HOUR": (GOLDEN_HOUR_ALTITUDE_DEG, True),
    "GOLDEN_HOUR_EVENING": (GOLDEN_HOUR_ALTITUDE_DEG, False),
    # ISNA dawn (-15°) - used in some prayer time conventions
    "ISNA_DAWN": (ISNA_DAWN_ALTITUDE_DEG, True),
    # Alias for civil dusk
    "DUSK": (CIVIL_TWILIGHT_ALTITUDE_DEG, False),
    # Solar noon (transit, not altitude crossing)
    "SOLAR_NOON": (None, None),
}


# Module-level caches for filtered event lists
# Moved out of the Enum class to avoid mypy treating them as enum members
_rising_events_cache: list[SolarEvent] | None = None
_setting_events_cache: list[SolarEvent] | None = None
_altitude_events_cache: list[SolarEvent] | None = None


class SolarEvent(Enum):
    """Named solar events.

    Each event represents a specific solar altitude crossing or transit.
    Dawn events are on the rising branch (morning), dusk events on the setting branch (evening).

    Notes:
        - `DUSK` is an alias for `CIVIL_DUSK`.
        - `GOLDEN_HOUR` is the morning start of golden hour at -4° altitude.
        - `SOLAR_NOON` is a transit event (sun at highest point), not an altitude-crossing event.
        - `ISNA_DAWN` uses the ISNA convention (-15°) for prayer time calculations.

    Example:
        >>> SolarEvent.SUNRISE.altitude
        -0.833
        >>> SolarEvent.SUNRISE.rising
        True
        >>> SolarEvent.SOLAR_NOON.altitude is None
        True
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

    ISNA_DAWN = "isna_dawn"

    DUSK = "dusk"
    SOLAR_NOON = "solar_noon"

    @property
    def altitude(self) -> float | None:
        """Solar altitude in degrees for this event, or None for transit events.

        Returns:
            Altitude in degrees (negative for below horizon), or None for SOLAR_NOON.
        """
        return _EVENT_DATA[self.name][0]

    @property
    def rising(self) -> bool | None:
        """Whether this event occurs on the rising (morning) or setting (evening) branch.

        Returns:
            True for dawn/rising events, False for dusk/setting events, None for SOLAR_NOON.
        """
        return _EVENT_DATA[self.name][1]

    @property
    def is_transit(self) -> bool:
        """True if this is a transit event (solar noon), not an altitude crossing."""
        return self is SolarEvent.SOLAR_NOON

    @property
    def is_dawn(self) -> bool:
        """True if this is a dawn/morning event (rising branch, not solar noon)."""
        return self.rising is True

    @property
    def is_dusk(self) -> bool:
        """True if this is a dusk/evening event (setting branch)."""
        return self.rising is False

    @classmethod
    def all(cls) -> list[SolarEvent]:
        """Return all solar events in declaration order."""
        return list(cls)

    @classmethod
    def rising_events(cls) -> list[SolarEvent]:
        """Return all events on the rising (morning) branch, excluding SOLAR_NOON."""
        global _rising_events_cache
        if _rising_events_cache is None:
            _rising_events_cache = [e for e in cls if e.rising is True]
        return _rising_events_cache

    @classmethod
    def setting_events(cls) -> list[SolarEvent]:
        """Return all events on the setting (evening) branch."""
        global _setting_events_cache
        if _setting_events_cache is None:
            _setting_events_cache = [e for e in cls if e.rising is False]
        return _setting_events_cache

    @classmethod
    def altitude_events(cls) -> list[SolarEvent]:
        """Return all events that have a defined altitude (excludes SOLAR_NOON)."""
        global _altitude_events_cache
        if _altitude_events_cache is None:
            _altitude_events_cache = [e for e in cls if e.altitude is not None]
        return _altitude_events_cache

    @classmethod
    def by_altitude(cls, altitude: float, rising: bool | None = None) -> list[SolarEvent]:
        """Find events matching a specific altitude and optionally rising/setting branch.

        Args:
            altitude: Target altitude in degrees.
            rising: Filter by branch (True=rising, False=setting, None=both).

        Returns:
            List of matching events (may be empty).
        """
        return [e for e in cls if e.altitude == altitude and (rising is None or e.rising == rising)]

    @classmethod
    def from_string(cls, name: str) -> SolarEvent | None:
        """Case-insensitive lookup by event name or value.

        Args:
            name: Event name (e.g., "SUNRISE") or value (e.g., "sunrise").

        Returns:
            Matching SolarEvent or None if not found.
        """
        name_lower = name.lower()
        for event in cls:
            if event.name.lower() == name_lower or event.value == name_lower:
                return event
        return None


@dataclass(frozen=True, slots=True)
class SolarAngle:
    """Custom solar altitude target.

    Use this for non-standard altitudes not covered by `SolarEvent`.

    Attributes:
        altitude: Target sun altitude in degrees (negative = below horizon).
        rising: True for ascending branch (morning), False for descending (evening).
        name: Optional human-readable label for debugging/display.

    Example:
        >>> angle = SolarAngle(altitude=-10.0, rising=True, name="custom_dawn")
        >>> angle.altitude
        -10.0
        >>> angle.rising
        True
    """

    altitude: float
    rising: bool = True
    name: str = "custom_solar_angle"

    def __post_init__(self) -> None:
        """Validate altitude is within reasonable bounds."""
        if not -90.0 <= self.altitude <= 90.0:
            raise ValueError(f"Altitude must be between -90 and 90 degrees, got {self.altitude}")

    def __str__(self) -> str:
        branch = "rising" if self.rising else "setting"
        return f"{self.name} ({self.altitude}°, {branch})"

    def __repr__(self) -> str:
        return f"SolarAngle(altitude={self.altitude}, rising={self.rising}, name={self.name!r})"

    @classmethod
    def from_event(cls, event: SolarEvent) -> SolarAngle:
        """Create a custom angle from a named solar event.

        Args:
            event: The solar event to convert.

        Returns:
            SolarAngle with the same altitude and rising branch.

        Raises:
            ValueError: If the event is SOLAR_NOON (no altitude/rising defined).
        """
        altitude = event.altitude
        rising = event.rising
        if altitude is None or rising is None:
            raise ValueError(f"Cannot convert {event!r} into a fixed solar angle (transit event).")
        return cls(altitude=altitude, rising=rising, name=event.value)

    def to_event(self) -> SolarEvent | None:
        """Find a matching SolarEvent if one exists for this angle.

        Returns:
            Matching SolarEvent or None if no standard event matches.
        """
        matches = SolarEvent.by_altitude(self.altitude, self.rising)
        return matches[0] if matches else None


# Type alias for any solar target (named event or custom angle)
SolarTarget = SolarEvent | SolarAngle
