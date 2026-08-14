#!/usr/bin/env python3
"""Water the garden at sunrise + 30 min, but only if clear and not too windy."""

import asyncio
from datetime import timedelta

from zeitwerkzeug import ExecutionLoop, FuzzyCron, Location, schedule
from zeitwerkzeug.context import All, SunAltitudeAbove
from zeitwerkzeug.integrations.weather import ClearWeather

LOCATION = Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")
MAX_CLOUD_COVER = 40
MAX_WIND_SPEED = 15


def water_plants(ctx):
    print(f"💧 Watering garden at {ctx.triggered_at} (attempt {ctx.attempt})")


async def main():

    trigger = (
        schedule.at("sunrise", location=LOCATION)
        .offset(minutes=30)
        .require(
            All(
                ClearWeather(
                    lat=LOCATION.lat,
                    lon=LOCATION.lon,
                    max_cloud_cover=MAX_CLOUD_COVER,
                ),
                SunAltitudeAbove(LOCATION, min_altitude=-6.0),
            )
        )
        .on_fail(
            retry_interval=timedelta(minutes=15),
            max_attempts=3,
            limit=timedelta(hours=2),
        )
    )

    # Register and run
    cron = FuzzyCron()
    cron.register(water_plants, trigger, name="garden-irrigation")

    loop = ExecutionLoop(
        registry=cron,
        default_job_timeout=timedelta(seconds=30),
        max_concurrency=2,
    )

    print("🌱 Garden irrigation daemon started")
    await loop.run()


if __name__ == "__main__":
    asyncio.run(main())
