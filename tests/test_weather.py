"""Tests for the Open-Meteo weather integration."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

from zeitwerkzeug import ClearWeather
from zeitwerkzeug.interfaces import ExecutionContext


@pytest.mark.asyncio
async def test_clear_weather_true_when_cloud_cover_is_low(
    monkeypatch: pytest.MonkeyPatch,
    fake_weather_client_factory,
    clear_weather_osaka: ClearWeather,
    execution_context: ExecutionContext,
) -> None:
    monkeypatch.setattr(
        "zeitwerkzeug.integrations.weather.httpx.AsyncClient",
        fake_weather_client_factory({"current_weather": {"cloudcover": 10}}),
    )

    result = await clear_weather_osaka.evaluate(execution_context)
    assert result is True


@pytest.mark.asyncio
async def test_clear_weather_false_when_cloud_cover_is_high(
    monkeypatch: pytest.MonkeyPatch,
    fake_weather_client_factory,
    clear_weather_osaka: ClearWeather,
    execution_context: ExecutionContext,
) -> None:
    monkeypatch.setattr(
        "zeitwerkzeug.integrations.weather.httpx.AsyncClient",
        fake_weather_client_factory({"current_weather": {"cloudcover": 80}}),
    )

    result = await clear_weather_osaka.evaluate(execution_context)
    assert result is False


@pytest.mark.asyncio
async def test_clear_weather_false_when_api_returns_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_weather_error_client_factory,
    clear_weather_osaka: ClearWeather,
    execution_context: ExecutionContext,
) -> None:
    monkeypatch.setattr(
        "zeitwerkzeug.integrations.weather.httpx.AsyncClient",
        fake_weather_error_client_factory(500),
    )

    result = await clear_weather_osaka.evaluate(execution_context)
    assert result is False


@pytest.mark.asyncio
async def test_live_open_meteo_api_is_reachable() -> None:
    """
    Optional live test.

    This test is skipped unless you set::

        ZEITWERKZEUG_LIVE_WEATHER=1

    On PowerShell::

        $env:ZEITWERKZEUG_LIVE_WEATHER = "1"
        uv run pytest tests/test_weather.py -v
    """
    if os.getenv("ZEITWERKZEUG_LIVE_WEATHER") != "1":
        pytest.skip("Live weather test disabled. Set ZEITWERKZEUG_LIVE_WEATHER=1 to run.")

    params = {
        "latitude": 34.6937,
        "longitude": 135.5020,
        "current_weather": True,
    }

    timeout = httpx.Timeout(5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params,
        )

    assert response.status_code == 200

    data = response.json()

    assert "current_weather" in data
    assert "windspeed" in data["current_weather"]


@pytest.mark.asyncio
async def test_live_clear_weather_plugin() -> None:
    """
    Optional live test for the ClearWeather plugin itself.

    This uses max_cloud_cover=100 so it should usually pass
    as long as the API is reachable.
    """
    if os.getenv("ZEITWERKZEUG_LIVE_WEATHER") != "1":
        pytest.skip("Live weather test disabled. Set ZEITWERKZEUG_LIVE_WEATHER=1 to run.")

    condition = ClearWeather(
        lat=34.6937,
        lon=135.5020,
        max_cloud_cover=100,
    )

    now = datetime.now(UTC)
    context = ExecutionContext(
        job_name="live-weather-test",
        scheduled_for=now,
        triggered_at=now,
        attempt=1,
    )

    result = await condition.evaluate(context)
    assert result is True