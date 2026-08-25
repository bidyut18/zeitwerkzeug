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

        # (window, max_calls, period_seconds) triples defining each limit tier.
        self._windows: list[tuple[deque[float], int, float]] = [
            (deque(), max_per_minute, 60.0),
            (deque(), max_per_hour, 3600.0),
            (deque(), max_per_day, 86400.0),
        ]

        self._lock = asyncio.Lock()

    @staticmethod
    def _prune(window: deque[float], now: float, period: float) -> None:
        """Drop timestamps that have fallen outside the sliding window."""
        while window and now - window[0] >= period:
            window.popleft()

    async def check_and_acquire(self) -> bool:
        """
        Returns True if a request is allowed and records it.
        Returns False if the safety margin has been hit.
        """
        async with self._lock:
            now = time.monotonic()

            for window, _, period in self._windows:
                self._prune(window, now, period)

            if any(len(window) >= max_calls for window, max_calls, _ in self._windows):
                counts = [len(window) for window, _, _ in self._windows]
                logger.warning(
                    "Open-Meteo free-tier rate limit reached. "
                    "Counts: %d/min, %d/hr, %d/day. "
                    "Failing condition to prevent IP ban. "
                    "Consider upgrading to a commercial API key.",
                    *counts,
                )
                return False

            for window, _, _ in self._windows:
                window.append(now)
            return True


FREE_API_LIMITER = OpenMeteoRateLimiter()
