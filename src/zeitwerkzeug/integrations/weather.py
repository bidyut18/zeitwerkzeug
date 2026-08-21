"""Weather condition plugins using httpx and Open-Meteo."""

from __future__ import annotations

import logging

import httpx

from zeitwerkzeug.integrations.rate_limit import FREE_API_LIMITER, OpenMeteoRateLimiter
from zeitwerkzeug.interfaces import ExecutionContext

logger = logging.getLogger(__name__)


class ClearWeather:
    """Requires cloud cover to be below a threshold."""

    def __init__(
        self,
        lat: float,
        lon: float,
        max_cloud_cover: int,
        api_key: str | None = None,
    ) -> None:
        self.lat = lat
        self.lon = lon
        self.api_key: str | None = None
        self.max_cloud_cover = max_cloud_cover
        self.limiter: OpenMeteoRateLimiter | None

        if api_key:
            # Commercial endpoint has practically unlimited quotas
            self.endpoint = "https://customer-api.open-meteo.com/v1/forecast"
            self.api_key = api_key
            self.limiter = None
        else:
            # Free endpoint requires strict rate limiting
            self.endpoint = "https://api.open-meteo.com/v1/forecast"
            self.api_key = None
            self.limiter = FREE_API_LIMITER

    async def evaluate(self, context: ExecutionContext) -> bool:
        logger.debug(
            "Checking weather for job=%s attempt=%s",
            context.job_name,
            context.attempt,
        )

        if self.limiter is not None:
            allowed = await self.limiter.check_and_acquire()
            if not allowed:
                logger.warning(
                    f"Unfortunately,Rate limit is over.currently- {self.limiter.max_per_hour}/hr",
                )
                return False

        headers = {"User-Agent": "Zeitwerkzeug-Python-Library/0.0.1"}
        params: dict[str, bool | float | str] = {
            "latitude": self.lat,
            "longitude": self.lon,
            "current_weather": True,
        }

        if self.api_key:
            params["apikey"] = self.api_key

        timeout = httpx.Timeout(5.0)

        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            try:
                response = await client.get(self.endpoint, params=params)
                response.raise_for_status()
                data = response.json()

                cloud_cover = int(data.get("current_weather", {}).get("cloudcover", 100))
                return cloud_cover <= self.max_cloud_cover

            except httpx.HTTPError:
                logger.warning("Weather API request failed for job=%s", context.job_name)
                return False
