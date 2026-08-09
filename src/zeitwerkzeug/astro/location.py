"""Location model used throughout the library."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC
from datetime import timezone as _datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zeitwerkzeug.exceptions import LocationError


def _resolve_tzinfo(name: str) -> ZoneInfo | _datetime_timezone:
    if name == "UTC" or name in {"Etc/UTC", "Etc/GMT"}:
        return UTC
    return ZoneInfo(name)


@dataclass(frozen=True, slots=True)
class Location:
    """Geographic location with an IANA timezone.

    Attributes:
        lat: Latitude in decimal degrees, from -90 to 90.
        lon: Longitude in decimal degrees, from -180 to 180.
        timezone: IANA timezone name, e.g. "Asia/Kolkata".
        elevation_m: Optional elevation in meters. Currently reserved for
            future refraction/pressure refinements.
        name: Optional display name.
    """

    lat: float
    lon: float
    timezone: str = "UTC"
    elevation_m: float = 0.0
    name: str | None = None
    _tzinfo: ZoneInfo = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise LocationError(f"Invalid latitude: {self.lat!r}")

        if not -180.0 <= self.lon <= 180.0:
            raise LocationError(f"Invalid longitude: {self.lon!r}")

        try:
            object.__setattr__(self, "_tzinfo", _resolve_tzinfo(self.timezone))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise LocationError(f"Invalid timezone: {self.timezone!r}") from exc

    @property
    def tzinfo(self) -> ZoneInfo | _datetime_timezone:
        """Return the resolved timezone object."""
        return self._tzinfo

    @property
    def key(self) -> tuple[float, float, str]:
        """Cache-friendly identity for this location."""
        return (
            round(self.lat, 6),
            round(self.lon, 6),
            self.timezone,
        )
