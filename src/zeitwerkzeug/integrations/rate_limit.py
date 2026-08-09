"""Asyncio-safe sliding window rate limiter for Open-Meteo."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class OpenMeteoRateLimiter:
    """
    Enforces Open-Meteo free tier limits with a built-in safety margin.
    
    Open-Meteo Hard Limits: 600/min, 5000/hr, 10000/day.
    Our Library Hard Caps:  500/min, 4500/hr, 9000/day.
    """

    def __init__(
        self,
        max_per_minute: int = 500,
        max_per_hour: int = 4500,
        max_per_day: int = 9000,
    ) -> None:
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day

        self._minute_calls: deque[float] = deque()
        self._hour_calls: deque[float] = deque()
        self._day_calls: deque[float] = deque()

        self._lock = asyncio.Lock()

    async def check_and_acquire(self) -> bool:
        """
        Returns True if a request is allowed and records it.
        Returns False if the safety margin has been hit.
        """
        async with self._lock:
            now = time.monotonic()

            # Slide the windows: remove timestamps older than their respective windows
            while self._minute_calls and now - self._minute_calls[0] >= 60.0:
                self._minute_calls.popleft()
            while self._hour_calls and now - self._hour_calls[0] >= 3600.0:
                self._hour_calls.popleft()
            while self._day_calls and now - self._day_calls[0] >= 86400.0:
                self._day_calls.popleft()

            # Check against our strict safety caps
            if (
                len(self._minute_calls) >= self.max_per_minute
                or len(self._hour_calls) >= self.max_per_hour
                or len(self._day_calls) >= self.max_per_day
            ):
                logger.warning(
                    "Open-Meteo free-tier rate limit reached. "
                    "Counts: %d/min, %d/hr, %d/day. "
                    "Failing condition to prevent IP ban. "
                    "Consider upgrading to a commercial API key.",
                    len(self._minute_calls),
                    len(self._hour_calls),
                    len(self._day_calls),
                )
                return False

            # Record the call
            self._minute_calls.append(now)
            self._hour_calls.append(now)
            self._day_calls.append(now)
            return True


# Global singleton used by all free-tier ClearWeather instances
FREE_API_LIMITER = OpenMeteoRateLimiter()