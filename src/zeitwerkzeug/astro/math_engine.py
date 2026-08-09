"""Pure solar-geometry engine.

This module uses a compact NOAA/Meeus-style solar algorithm that is:
- fast
- dependency-free
- sufficiently accurate for scheduling use cases

Performance notes:
- core scalar helpers are cached with functools.lru_cache
- only pure standard-library math is used
- datetime/timezone handling is isolated at the edges
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import NamedTuple

from zeitwerkzeug.astro.events import SolarAngle, SolarEvent, SolarTarget
from zeitwerkzeug.astro.location import Location
from zeitwerkzeug.exceptions import SolarEventNotFoundError

_J2000_JD = 2451545.0
_SECONDS_PER_DAY = 86400.0


class SunGeometry(NamedTuple):
    """Intermediate solar geometry values."""

    declination: float
    equation_of_time_minutes: float
    true_longitude: float
    obliquity: float


def _ensure_utc(dt_utc: datetime) -> datetime:
    if dt_utc.tzinfo is None:
        return dt_utc.replace(tzinfo=UTC)
    return dt_utc.astimezone(UTC)


def julian_day(dt_utc: datetime) -> float:
    """Convert a UTC datetime to Julian Day."""
    dt_utc = _ensure_utc(dt_utc)
    return 2440587.5 + dt_utc.timestamp() / _SECONDS_PER_DAY


def _normalize_degrees(value: float) -> float:
    return value % 360.0


def solar_geometry(dt_utc: datetime) -> SunGeometry:
    """Compute solar declination and equation of time for a UTC datetime.

    This is a compact implementation based on standard astronomical series.
    """
    dt_utc = _ensure_utc(dt_utc)

    sin = math.sin
    cos = math.cos
    tan = math.tan
    asin = math.asin
    radians = math.radians
    degrees = math.degrees

    jd = 2440587.5 + dt_utc.timestamp() / _SECONDS_PER_DAY
    t = (jd - _J2000_JD) / 36525.0

    # Geometric mean longitude of the Sun (deg)
    l0 = _normalize_degrees(280.46646 + t * (36000.76983 + t * 0.0003032))

    # Mean anomaly of the Sun (deg)
    m = _normalize_degrees(357.52911 + t * (35999.05029 - 0.0001537 * t))

    # Eccentricity of Earth's orbit
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m_rad = radians(m)

    # Equation of center (deg)
    c = (
        sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + sin(2.0 * m_rad) * (0.019993 - 0.000101 * t)
        + sin(3.0 * m_rad) * 0.000289
    )

    true_longitude = l0 + c

    omega = 125.04 - 1934.136 * t
    omega_rad = radians(omega)

    apparent_longitude = true_longitude - 0.00569 - 0.00478 * sin(omega_rad)

    mean_obliquity = (
        23.0
        + (26.0 + (21.448 - t * (46.8150 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    )

    obliquity = mean_obliquity + 0.00256 * cos(omega_rad)

    declination = degrees(
        asin(math.sin(radians(obliquity)) * math.sin(radians(apparent_longitude)))
    )

    y = tan(radians(obliquity) / 2.0) ** 2
    l0_rad = radians(l0)

    equation_of_time_minutes = 4.0 * degrees(
        y * sin(2.0 * l0_rad)
        - 2.0 * e * sin(m_rad)
        + 4.0 * e * y * sin(m_rad) * cos(2.0 * l0_rad)
        - 0.5 * y * y * sin(4.0 * l0_rad)
        - 1.25 * e * e * sin(2.0 * m_rad)
    )

    return SunGeometry(
        declination=declination,
        equation_of_time_minutes=equation_of_time_minutes,
        true_longitude=true_longitude,
        obliquity=obliquity,
    )


def _hour_angle_deg(
    latitude: float,
    declination: float,
    altitude: float,
) -> float | None:
    """Return hour angle in degrees for a target solar altitude.

    Returns None if the target altitude is not crossed on that day.
    """
    lat_rad = math.radians(latitude)
    dec_rad = math.radians(declination)
    alt_rad = math.radians(altitude)

    cos_lat = math.cos(lat_rad)
    cos_dec = math.cos(dec_rad)
    denominator = cos_lat * cos_dec

    numerator = math.sin(alt_rad) - math.sin(lat_rad) * math.sin(dec_rad)

    if abs(denominator) < 1e-12:
        # Polar singularity. If numerator is also near zero, treat as boundary.
        if abs(numerator) <= 1e-12:
            return 0.0
        return None

    cos_h = numerator / denominator

    if cos_h > 1.0 or cos_h < -1.0:
        return None

    return math.degrees(math.acos(cos_h))


def _utc_midnight(utc_date: date) -> datetime:
    return datetime.combine(utc_date, time.min, tzinfo=UTC)


def _event_minutes_on_utc_date(
    utc_date: date,
    latitude: float,
    longitude: float,
    altitude: float,
    rising: bool,
) -> float | None:
    """Approximate event time in minutes relative to UTC midnight."""
    base = _utc_midnight(utc_date)

    # Initial estimate near solar noon.
    minutes = 720.0 - 4.0 * longitude

    for _ in range(2):
        approx = base + timedelta(minutes=minutes)
        geom = solar_geometry(approx)

        solar_noon_minutes = 720.0 - 4.0 * longitude - geom.equation_of_time_minutes
        hour_angle = _hour_angle_deg(latitude, geom.declination, altitude)

        if hour_angle is None:
            return None

        minutes = solar_noon_minutes + (
            -4.0 * hour_angle if rising else 4.0 * hour_angle
        )

    if not math.isfinite(minutes):
        return None

    return minutes


def _transit_minutes_on_utc_date(
    utc_date: date,
    longitude: float,
) -> float:
    """Approximate solar noon in minutes relative to UTC midnight."""
    base = _utc_midnight(utc_date)
    minutes = 720.0 - 4.0 * longitude

    for _ in range(2):
        approx = base + timedelta(minutes=minutes)
        geom = solar_geometry(approx)
        minutes = 720.0 - 4.0 * longitude - geom.equation_of_time_minutes

    return minutes


@lru_cache(maxsize=32768)
def _cached_event_minutes(
    utc_date: date,
    latitude: float,
    longitude: float,
    altitude: float,
    rising: bool,
) -> float | None:
    return _event_minutes_on_utc_date(utc_date, latitude, longitude, altitude, rising)


@lru_cache(maxsize=32768)
def _cached_transit_minutes(
    utc_date: date,
    longitude: float,
) -> float:
    return _transit_minutes_on_utc_date(utc_date, longitude)


def event_utc_datetime(
    local_date: date,
    location: Location,
    altitude: float,
    rising: bool,
) -> datetime:
    """Return the UTC datetime for a solar altitude crossing on a local date."""
    tzinfo = location.tzinfo
    lat_key, lon_key, _ = location.key
    altitude_key = round(float(altitude), 4)

    local_start = datetime.combine(local_date, time.min, tzinfo=tzinfo)
    local_end = local_start + timedelta(days=1)

    start_utc = local_start.astimezone(UTC)
    end_utc = local_end.astimezone(UTC)

    candidate_dates: set[date] = {
        start_utc.date(),
        end_utc.date(),
    }

    # Sample several local hours to handle timezone offsets near date boundaries.
    for hour in (0, 6, 12, 18, 23):
        probe_local = datetime.combine(local_date, time(hour, 0), tzinfo=tzinfo)
        candidate_dates.add(probe_local.astimezone(UTC).date())

    for utc_date in sorted(candidate_dates):
        minutes = _cached_event_minutes(
            utc_date, lat_key, lon_key, altitude_key, rising
        )

        if minutes is None or not math.isfinite(minutes):
            continue

        dt_utc = _utc_midnight(utc_date) + timedelta(minutes=minutes)

        if start_utc <= dt_utc < end_utc:
            return dt_utc

    raise SolarEventNotFoundError(
        f"No solar event for local_date={local_date}, altitude={altitude}, rising={rising}, "
        f"location={location!r}"
    )


def solar_noon_utc_datetime(local_date: date, location: Location) -> datetime:
    """Return solar noon as a UTC datetime for a local date."""
    tzinfo = location.tzinfo
    _, lon_key, _ = location.key

    local_start = datetime.combine(local_date, time.min, tzinfo=tzinfo)
    local_end = local_start + timedelta(days=1)

    start_utc = local_start.astimezone(UTC)
    end_utc = local_end.astimezone(UTC)

    candidate_dates: set[date] = {
        start_utc.date(),
        end_utc.date(),
    }

    for hour in (0, 6, 12, 18, 23):
        probe_local = datetime.combine(local_date, time(hour, 0), tzinfo=tzinfo)
        candidate_dates.add(probe_local.astimezone(UTC).date())

    for utc_date in sorted(candidate_dates):
        minutes = _cached_transit_minutes(utc_date, lon_key)
        dt_utc = _utc_midnight(utc_date) + timedelta(minutes=minutes)

        if start_utc <= dt_utc < end_utc:
            return dt_utc

    raise SolarEventNotFoundError(
        f"No solar noon for local_date={local_date}, location={location!r}"
    )


def event_from_target_utc(
    local_date: date,
    location: Location,
    target: SolarTarget,
) -> datetime:
    """Resolve a solar target to a UTC datetime for a local date."""
    if isinstance(target, SolarEvent):
        if target is SolarEvent.SOLAR_NOON:
            return solar_noon_utc_datetime(local_date, location)

        altitude = target.altitude
        rising = target.rising

        if altitude is None or rising is None:
            raise SolarEventNotFoundError(f"Cannot resolve solar target: {target!r}")

        return event_utc_datetime(local_date, location, altitude, rising)

    if isinstance(target, SolarAngle):
        return event_utc_datetime(local_date, location, target.altitude, target.rising)

    raise TypeError(f"Unsupported solar target: {target!r}")


def next_solar_event_utc(
    after: datetime,
    location: Location,
    target: SolarTarget,
    *,
    search_days: int = 400,
) -> datetime:
    """Find the next UTC occurrence of a solar target after a datetime."""
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    else:
        after = after.astimezone(UTC)

    local_tz = location.tzinfo
    local_after = after.astimezone(local_tz)
    start_date = local_after.date()

    for offset in range(search_days + 1):
        local_date = start_date + timedelta(days=offset)

        try:
            candidate = event_from_target_utc(local_date, location, target)
        except SolarEventNotFoundError:
            continue

        if candidate > after:
            return candidate

    raise SolarEventNotFoundError(
        f"No future occurrence for target={target!r}, location={location!r}, after={after!r}"
    )


def sun_altitude(dt_utc: datetime, location: Location) -> float:
    """Return current sun altitude in degrees for a datetime and location."""
    dt_utc = _ensure_utc(dt_utc)

    geom = solar_geometry(dt_utc)
    midnight = _utc_midnight(dt_utc.date())
    minutes_since_midnight = (dt_utc - midnight).total_seconds() / 60.0

    true_solar_time_minutes = (
        minutes_since_midnight + geom.equation_of_time_minutes + 4.0 * location.lon
    )

    hour_angle_deg = (true_solar_time_minutes - 720.0) / 4.0

    lat_rad = math.radians(location.lat)
    dec_rad = math.radians(geom.declination)
    ha_rad = math.radians(hour_angle_deg)

    altitude_rad = math.asin(
        math.sin(lat_rad) * math.sin(dec_rad)
        + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    )

    return math.degrees(altitude_rad)
