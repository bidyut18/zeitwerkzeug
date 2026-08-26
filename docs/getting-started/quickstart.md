# Quickstart

!!! quote "In one sentence"
    Build a solar-powered personal assistant in five small steps.

By the end you'll have three jobs — open blinds at sunrise, water plants at
golden hour (only if clear), and close blinds at dusk — running in one daemon.

## Step 1 — Define your location

Everything solar starts with a `Location`.

```python
from zeitwerkzeug import Location

BERLIN = Location(lat=52.52, lon=13.405, timezone="Europe/Berlin")  # (1)
```

1. Latitude, longitude and an IANA timezone. All solar math **and** time windows use this.

## Step 2 — Build schedules with the fluent API

```python
from datetime import time, timedelta

from zeitwerkzeug import ScheduleBuilder, SolarEvent, SunAltitudeAbove, TimeWindow

schedule = ScheduleBuilder()

sunrise = (
    schedule.at(SolarEvent.SUNRISE, location=BERLIN)  # (1)
    .require(
        SunAltitudeAbove(location=BERLIN, min_altitude=0.0),  # (2)
        TimeWindow(start=time(6, 0), end=time(22, 0), tz="Europe/Berlin"),  # (3)
    )
    .on_fail(retry_interval=timedelta(minutes=5), max_attempts=3)  # (4)
)
```

1. **When** — fire at sunrise in Berlin, recomputed daily.
2. **Only if** — the sun is actually above the horizon.
3. **And only if** — it's a sane hour (06:00–22:00).
4. **If it fails** — retry every 5 minutes, up to 3 times.

Add two more triggers:

```python
from zeitwerkzeug import ClearWeather

golden_hour = (
    schedule.at(SolarEvent.GOLDEN_HOUR, location=BERLIN)
    .require(ClearWeather(lat=BERLIN.lat, lon=BERLIN.lon, max_cloud_cover=30))
    .on_fail(retry_interval=timedelta(minutes=15), max_attempts=2)
)

dusk = schedule.at(SolarEvent.CIVIL_DUSK, location=BERLIN).on_fail(
    retry_interval=timedelta(minutes=10), max_attempts=5
)
```

!!! warning
    `ClearWeather` needs the weather extra: `uv add "zeitwerkzeug[weather]"`.

## Step 3 — Define your jobs

Jobs are plain async functions that receive a context object.

```python
async def open_blinds(ctx):
    print(f"🌅 Opening blinds at {ctx.triggered_at.isoformat()}")


async def water_plants(ctx):
    print(f"🌱 Watering plants at {ctx.triggered_at.isoformat()}")


async def close_blinds(ctx):
    print(f"🌇 Closing blinds at {ctx.triggered_at.isoformat()}")
```

## Step 4 — Register the jobs

```python
from zeitwerkzeug import FuzzyCron

FuzzyCron.add_job(open_blinds, trigger=sunrise, name="open_blinds", pass_context=True)
FuzzyCron.add_job(water_plants, trigger=golden_hour, name="water_plants", pass_context=True)
FuzzyCron.add_job(close_blinds, trigger=dusk, name="close_blinds", pass_context=True)
```

## Step 5 — Run the daemon

```python
import asyncio
from datetime import UTC, datetime

from zeitwerkzeug import ExecutionLoop


async def main():
    loop = ExecutionLoop(
        max_concurrency=4,  # (1)
        default_job_timeout=timedelta(minutes=2),  # (2)
        midnight_recalibration=True,  # (3)
    )

    await loop.run(until=datetime.now(UTC) + timedelta(minutes=5))  # (4)

    for record in loop.history:
        print(f"{record.job_name:20} | {record.status:15} | attempt={record.attempt}")


asyncio.run(main())
```

1. Never run more than 4 jobs at once.
2. Kill any job that hangs longer than 2 minutes.
3. Re-resolve all solar triggers at midnight, per timezone.
4. Demo only — in production just call `await loop.run()`.

## What you should see

```text
🌅 Opening blinds at 2026-08-26T06:12:04+02:00
🌱 Watering plants at 2026-08-26T19:41:33+02:00
🌇 Closing blinds at 2026-08-26T20:24:51+02:00

open_blinds          | success         | attempt=1
water_plants         | success         | attempt=1
close_blinds         | success         | attempt=1
```

## Where to go next

-   [Solar time →](../concepts/astro.md) understand how sunrise is computed.
-   [Conditions →](../concepts/context.md) gate jobs with weather and logic.
-   [Persistence →](../concepts/persistence.md) survive restarts with SQLite.
