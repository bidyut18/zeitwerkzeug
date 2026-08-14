# Zeitwerkzeug
[![CI](https://github.com/bidyut18/zeitwerkzeug/actions/workflows/ci.yml/badge.svg)](https://github.com/bidyut18/zeitwerkzeug/actions/workflows/ci.yml)
 [![PyPI version](https://badge.fury.io/py/zeitwerkzeug.svg)](https://badge.fury.io/py/zeitwerkzeug)


<h3>⏰<i>Project is in initial state; Do not use it in production server. We are still experimenting on it  </i></h3>

**Contextual Time for Python**
Zeitwerkzeug (German for "time tool") is an experimental library that treats time not as a fixed stream of timestamps but as something derived from **solar geometry**, **human rhythms**, **environmental conditions**, and **user intent**. It's designed for building adaptive scheduling and automation systems.

---

## Features

- **Solar Geometry** – dawn, golden hour, dusk, and custom solar angles
- **Human Personas** – wake/sleep rhythms, weekend shifts, and proportional time blocks
- **Context-Aware Conditions** – weather, sun altitude, time windows, logical combinators
- **Async Execution Loop** – drifting schedules that recalibrate over time
- **Lazy Schedules** – fluent API for building complex triggers
- **Retry & Fail Policies** – automatic retries with configurable backoff
- **Rate-Limited Integrations** – Open-Meteo weather API with built‑in safety margins

---

## Installation

```bash
pip install zeitwerkzeug
```

For weather integrations (Open‑Meteo):

```bash
pip install "zeitwerkzeug[weather]"
```

---

## Quick Start

### Solar-Powered Personal Assistant

```python
"""
Zeitwerkzeug — Solar-Powered Personal Assistant
"""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta

from zeitwerkzeug import (
    ClearWeather,
    ExecutionLoop,
    FuzzyCron,
    Location,
    ScheduleBuilder,
    SolarEvent,
    SunAltitudeAbove,
    TimeWindow,
)

logging.basicConfig(level=logging.INFO)

# 1. Define your location
BERLIN = Location(lat=52.52, lon=13.405, timezone="Europe/Berlin")

# 2. Build schedules with the fluent API
schedule = ScheduleBuilder()

# Sunrise — open blinds, but only if the sun is actually up and it's a reasonable hour
sunrise = (
    schedule.at(SolarEvent.SUNRISE, location=BERLIN)
    .require(
        SunAltitudeAbove(location=BERLIN, min_altitude=0.0),
        TimeWindow(start=time(6, 0), end=time(22, 0), tz="Europe/Berlin"),
    )
    .on_fail(retry_interval_mins=5, max_attempts=3)
)

# Golden hour — water plants, but only if the weather is clear
golden_hour = (
    schedule.at(SolarEvent.GOLDEN_HOUR, location=BERLIN)
    .require(
        ClearWeather(lat=BERLIN.lat, lon=BERLIN.lon, max_cloud_cover=30),
    )
    .on_fail(retry_interval_mins=15, max_attempts=2)
)

# Civil dusk — close blinds every evening
dusk = schedule.at(SolarEvent.CIVIL_DUSK, location=BERLIN).on_fail(
    retry_interval=timedelta(minutes=10), max_attempts=5
)


# 3. Register jobs
async def open_blinds(ctx):
    print(f"🌅  Opening blinds at {ctx.triggered_at.isoformat()}")


async def water_plants(ctx):
    print(f"🌱  Watering plants at {ctx.triggered_at.isoformat()}")


async def close_blinds(ctx):
    print(f"🌇  Closing blinds at {ctx.triggered_at.isoformat()}")


FuzzyCron.add_job(open_blinds, trigger=sunrise, name="open_blinds", pass_context=True)
FuzzyCron.add_job(water_plants, trigger=golden_hour, name="water_plants", pass_context=True)
FuzzyCron.add_job(close_blinds, trigger=dusk, name="close_blinds", pass_context=True)


# 4. Run the daemon
async def main():
    loop = ExecutionLoop(
        max_concurrency=4,
        default_job_timeout=timedelta(minutes=2),
        midnight_recalibration=True,
    )
    # Run for 5 minutes in this demo; in production use `await loop.run()`
    await loop.run(until=datetime.now(UTC) + timedelta(minutes=5))

    print("\nExecution history:")
    for r in loop.history:
        print(f"  {r.job_name:20} | {r.status:15} | attempt={r.attempt}")


if __name__ == "__main__":
    asyncio.run(main())
```

### Solar Event with Custom Angle

```python
from zeitwerkzeug import Location, schedule
from zeitwerkzeug.astro import SolarAngle

location = Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")

# Custom solar angle: golden hour (-4°) on the rising branch
golden_hour = SolarAngle(altitude=-4.0, rising=True, name="golden_hour")

trigger = schedule.at(golden_hour, location=location)
```

### Human Persona & Time Windows

```python
from datetime import time, timedelta
from zeitwerkzeug import schedule, StandardWorker
from zeitwerkzeug.context import TimeWindow

persona = StandardWorker(tz="Asia/Tokyo")

# Schedule: 2 hours after waking, within a time window
trigger = schedule.at(lambda t: persona.wake_datetime(t) + timedelta(hours=2)).require(
    TimeWindow(start=time(6, 0), end=time(9, 0), tz="Asia/Tokyo")
)
```

### Logical Condition Combinators

```python
from zeitwerkzeug.context import All, Not, SunAltitudeAbove, TimeWindow

location = Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")

trigger = schedule.at("sunset", location=location).require(
    All(
        SunAltitudeAbove(location, min_altitude=-6.0),  # civil twilight
        Not(TimeWindow(start=time(0, 0), end=time(5, 0), tz="Asia/Tokyo")),
    )
)
```

### Async Job with Retry Policy

```python
from datetime import timedelta
from zeitwerkzeug import schedule, FuzzyCron, ExecutionLoop


async def fetch_weather(ctx):
    print(f"🌤️ Fetching weather at {ctx.triggered_at}")


location = Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")

trigger = schedule.at("sunrise", location=location).on_fail(
    retry_interval=timedelta(minutes=5), max_attempts=3
)

cron = FuzzyCron()
cron.register(fetch_weather, trigger, name="weather-fetch")

loop = ExecutionLoop(
    registry=cron,
    default_job_timeout=timedelta(seconds=30),
    max_concurrency=5,
)
# await loop.run()
```

---

## Key Concepts

### Schedules (`LazySchedule`)

A schedule is a lazily‑resolved trigger. Build one with the `schedule` builder:

```python
schedule.at(target, location=None, tz=None)
```

**Supported targets:**

| Type | Example |
|------|---------|
| `SolarEvent` | `"sunrise"`, `"sunset"`, `"golden_hour"` |
| `SolarAngle` | `SolarAngle(altitude=-4.0, rising=True)` |
| `datetime` | `datetime(2026, 1, 1, 12, 0, tzinfo=UTC)` |
| `time` | `time(14, 30)` (daily recurring) |
| `Callable` | `lambda t: t + timedelta(hours=1)` |

**Chaining methods:**

- `.require(*conditions)` – add required conditions
- `.on_fail(retry_interval=..., max_attempts=..., limit=...)` – attach retry policy

### Conditions (`ConditionPlugin`)

Conditions are evaluated immediately before job execution. Built‑in conditions include:

| Condition | Description |
|-----------|-------------|
| `SunAltitudeAbove(location, min_altitude)` | Sun altitude ≥ threshold |
| `TimeWindow(start, end, tz)` | Time within a local window |
| `ClearWeather(lat, lon, max_cloud_cover)` | Cloud cover ≤ threshold (requires `[weather]` extra) |
| `All(*conditions)` | Logical AND |
| `Any(*conditions)` | Logical OR |
| `Not(condition)` | Logical NOT |

### Persona Profiles

Model human daily rhythms with wake/sleep anchors.

```python
from zeitwerkzeug.personas import StandardWorker, NightShift, PersonaProfile

# Built‑in profiles
worker = StandardWorker(wake="06:30", sleep="22:30", tz="Asia/Tokyo")
night = NightShift(wake="13:00", sleep="05:00", tz="Asia/Tokyo")

# Custom profile
custom = PersonaProfile(
    wake=time(8, 0),
    sleep=time(0, 0),
    tz="Asia/Tokyo",
    weekend_wake_shift=timedelta(hours=2),
    weekend_sleep_shift=timedelta(hours=2),
)
```

**Available methods:**
- `wake_datetime(reference)` – wake time for a reference date
- `sleep_datetime(reference)` – sleep time (next day if needed)
- `awake_block(reference)` – full awake window
- `proportional_block(reference, start_frac, end_frac)` – fractional window

### Fail & Retry Policies

Attach a retry policy to a schedule:

```python
schedule.at("sunset", location=location).on_fail(
    retry_interval=timedelta(minutes=5),  # between retries
    max_attempts=3,  # total attempts
    limit=datetime(2026, 1, 1),  # stop trying after this time
    # limit can also be "sunrise", timedelta, or time
)
```

### Execution Loop

The `ExecutionLoop` runs the scheduler with these capabilities:

- **Drifting schedules** – `resolve_after()` recalculates on each run
- **Midnight recalibration** – re‑evaluates schedules daily per timezone
- **Concurrency control** – semaphore‑based limit (default 32)
- **History** – retains execution records (configurable limit)
- **Graceful shutdown** – via `stop()`

```python
loop = ExecutionLoop(
    registry=FuzzyCron(),
    clock=SystemClock(),
    max_concurrency=32,
    default_job_timeout=timedelta(minutes=5),
    default_condition_timeout=timedelta(seconds=30),
    history_limit=1000,
)

# Run until a deadline
await loop.run(until=datetime(2026, 1, 1, tzinfo=UTC))
```

### Weather Integration (Open‑Meteo)

The `ClearWeather` condition uses the [Open‑Meteo API](https://open-meteo.com/).

- **Free tier** – ratelimited (safety margins: 500/min, 4500/hr, 9000/day)
- **Commercial** – pass your `api_key` for higher limits

```python
from zeitwerkzeug.integrations.weather import ClearWeather

condition = ClearWeather(
    lat=34.6937,
    lon=135.5020,
    max_cloud_cover=30,
    api_key="your_commercial_key",  # optional
)
```

**License & Attribution:** Weather data provided by [Open‑Meteo](https://open-meteo.com/). Used under the CC BY 4.0 license.

---

## Full Example: Smart Garden Irrigation

```python
#!/usr/bin/env python3
"""Water the garden at sunrise + 30 min, if clear and not too windy."""

import asyncio
from datetime import timedelta

from zeitwerkzeug import Location, schedule, FuzzyCron, ExecutionLoop
from zeitwerkzeug.context import All, SunAltitudeAbove
from zeitwerkzeug.integrations.weather import ClearWeather

# Osaka, Japan
LOCATION = Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")
MAX_CLOUD_COVER = 40


def water_plants(ctx):
    print(f"💧 Watering garden at {ctx.triggered_at} (attempt {ctx.attempt})")


async def main():
    trigger = (
        schedule.at("sunrise", location=LOCATION)
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
```

---

## Development

### Setup

```bash
# Install Task (https://taskfile.dev)
task install
task lint
task typecheck
task test
```

### Project Structure

```
src/zeitwerkzeug/
├── astro/          # Solar geometry engine
├── context/        # Scheduling primitives and conditions
├── daemon/         # Async execution loop and job registry
├── integrations/   # Third‑party integrations (weather)
├── personas/       # Human rhythm profiles and parser
├── exceptions.py   # Central error hierarchy
├── interfaces.py   # Protocol definitions
└── __init__.py     # Public API
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Contributing

Contributions are welcome! Please:

- Open an issue for bugs or feature requests
- Follow the existing code style (ruff, mypy)
- Include tests for new functionality
- Update documentation as needed

---

## Acknowledgments

- Solar calculations inspired by NOAA and Meeus algorithms
- Weather data provided by [Open‑Meteo](https://open-meteo.com/)
- Built for Python 3.11+ with `asyncio` and modern type hints

---
