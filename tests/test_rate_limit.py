"""Tests for the Open-Meteo rate limiter."""

from __future__ import annotations

import pytest

from zeitwerkzeug.integrations.rate_limit import OpenMeteoRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_calls_under_limit(
    rate_limiter: OpenMeteoRateLimiter,
) -> None:
    assert await rate_limiter.check_and_acquire() is True
    assert await rate_limiter.check_and_acquire() is True


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_minute_limit(
    rate_limiter: OpenMeteoRateLimiter,
) -> None:
    assert await rate_limiter.check_and_acquire() is True
    assert await rate_limiter.check_and_acquire() is True

    # Third call inside the same minute should be blocked.
    assert await rate_limiter.check_and_acquire() is False


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_day_limit(
    strict_daily_limiter: OpenMeteoRateLimiter,
) -> None:
    assert await strict_daily_limiter.check_and_acquire() is True
    assert await strict_daily_limiter.check_and_acquire() is True

    # Third call should be blocked because of the daily cap.
    assert await strict_daily_limiter.check_and_acquire() is False