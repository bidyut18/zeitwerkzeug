"""Astronomical constants and standard solar depression angles."""

from __future__ import annotations

from enum import Enum

#: Apparent altitude for sunrise/sunset, including refraction and solar radius.
HORIZON_APPARENT_ALTITUDE_DEG = -0.833

#: Common photographic golden-hour lower bound.
GOLDEN_HOUR_ALTITUDE_DEG = -4.0

#: Common civil twilight altitude.
CIVIL_TWILIGHT_ALTITUDE_DEG = -6.0

#: Nautical twilight altitude.
NAUTICAL_TWILIGHT_ALTITUDE_DEG = -12.0

#: Astronomical twilight altitude.
ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG = -18.0

#: ISNA-style dawn angle, used by some prayer-time conventions.
ISNA_DAWN_ALTITUDE_DEG = -15.0


class Twilight(float, Enum):
    """Common twilight angles as altitude values in degrees."""

    CIVIL = CIVIL_TWILIGHT_ALTITUDE_DEG
    NAUTICAL = NAUTICAL_TWILIGHT_ALTITUDE_DEG
    ASTRONOMICAL = ASTRONOMICAL_TWILIGHT_ALTITUDE_DEG
    ISNA = ISNA_DAWN_ALTITUDE_DEG