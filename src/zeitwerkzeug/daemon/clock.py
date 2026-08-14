"""Time sources and UTC normalization."""

from __future__ import annotations

from datetime import UTC, datetime


def _ensure_utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class SystemClock:
    """Default wall-clock time source."""

    def now(self) -> datetime:
        return datetime.now(UTC)
