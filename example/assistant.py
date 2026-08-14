from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta

from zeitwerkzeug import (
    ClearWeather,
    ExecutionLoop,
    FuzzyCron,
    Location,
    NightShift,
    PersonaParser,
    ScheduleBuilder,
    SolarEvent,
    SunAltitudeAbove,
    TimeWindow,
)
from zeitwerkzeug.context.scheduler import LazySchedule

# ---------------------------------------------------------------------------
# 1. Configure logging so we can see what the scheduler is doing
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("solar_assistant")


# ---------------------------------------------------------------------------
# 2. Define a location (Berlin, Germany)
# ---------------------------------------------------------------------------
BERLIN = Location(
    lat=52.5200,
    lon=13.4050,
    timezone="Europe/Berlin",
    name="Berlin Office",
)


# ---------------------------------------------------------------------------
# 3. Job callbacks — sync and async are both supported
# ---------------------------------------------------------------------------
async def open_blinds(ctx) -> None:
    """Called at sunrise."""
    logger.info("🌅  Opening blinds — sun is up at %s", ctx.triggered_at.isoformat())


async def close_blinds(ctx) -> None:
    """Called at civil dusk."""
    logger.info("🌇  Closing blinds — evening has arrived at %s", ctx.triggered_at.isoformat())


async def water_plants(ctx) -> None:
    """Called during golden hour if the weather is clear."""
    logger.info(
        "🌱  Watering plants — golden hour + clear skies at %s (attempt %s)",
        ctx.triggered_at.isoformat(),
        ctx.attempt,
    )


async def night_backup(ctx) -> None:
    """Called during the night-shift quiet hours."""
    logger.info(
        "💾  Running nightly backup at %s (attempt %s)",
        ctx.triggered_at.isoformat(),
        ctx.attempt,
    )


async def persona_reminder(ctx) -> None:
    """Called at a human-relative time parsed from natural language."""
    logger.info(
        "📬  Sending reminder at %s — parsed from persona profile",
        ctx.triggered_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# 4. Build schedules using the fluent API
# ---------------------------------------------------------------------------
schedule = ScheduleBuilder()

# 4a. Sunrise — open blinds every day at sunrise in Berlin
sunrise_schedule: LazySchedule = (
    schedule.at(SolarEvent.SUNRISE, location=BERLIN)
    .require(
        # Only open blinds if the sun is actually above the horizon
        SunAltitudeAbove(location=BERLIN, min_altitude=0.0),
        # And only on weekdays (08:00-23:00 local time)
        TimeWindow(start=time(8, 0), end=time(23, 0), tz="Europe/Berlin"),
    )
    .on_fail(retry_interval_mins=5, max_attempts=3)
)

# 4b. Civil dusk — close blinds every evening
dusk_schedule: LazySchedule = schedule.at(SolarEvent.CIVIL_DUSK, location=BERLIN).on_fail(
    retry_interval=timedelta(minutes=10), max_attempts=5
)

# 4c. Golden hour + clear weather — water plants only when it is nice out
#     (requires the `weather` extra: uv pip install zeitwerkzeug[weather])
golden_hour_schedule: LazySchedule = (
    schedule.at(SolarEvent.GOLDEN_HOUR, location=BERLIN)
    .require(
        # Only water if cloud cover is below 30 %
        ClearWeather(lat=BERLIN.lat, lon=BERLIN.lon, max_cloud_cover=30),
    )
    .on_fail(retry_interval_mins=15, max_attempts=2)
)

# 4d. Night-shift profile — run backups during the quiet hours
night_shift = NightShift(tz="Europe/Berlin")
parser = PersonaParser(night_shift)

# Parse "late afternoon" relative to the night-shift persona.
# For a night-shift worker this maps to ~15:00-17:00 local time.
late_afternoon_block = parser.parse("late afternoon")
logger.info(
    'Night-shift "late afternoon" resolved to %s - %s',
    late_afternoon_block.start.isoformat(),
    late_afternoon_block.end.isoformat(),
)

# Schedule a one-off reminder inside that block (using the block start)
persona_schedule: LazySchedule = schedule.at(late_afternoon_block.start, location=BERLIN).on_fail(
    retry_interval_mins=5, max_attempts=1
)


# ---------------------------------------------------------------------------
# 5. Register jobs with the global FuzzyCron registry
# ---------------------------------------------------------------------------
FuzzyCron.add_job(
    func=open_blinds,
    trigger=sunrise_schedule,
    name="open_blinds_sunrise",
    tags={"home", "morning"},
    pass_context=True,
    job_timeout=timedelta(seconds=30),
)

FuzzyCron.add_job(
    func=close_blinds,
    trigger=dusk_schedule,
    name="close_blinds_dusk",
    tags={"home", "evening"},
    pass_context=True,
)

FuzzyCron.add_job(
    func=water_plants,
    trigger=golden_hour_schedule,
    name="water_plants_golden_hour",
    tags={"garden", "weather_dependent"},
    pass_context=True,
    condition_timeout=timedelta(seconds=10),
)

FuzzyCron.add_job(
    func=night_backup,
    trigger=schedule.at(time(3, 0), tz="Europe/Berlin"),  # 03:00 local time
    name="nightly_backup",
    tags={"infra", "night"},
    pass_context=True,
    max_latency=timedelta(minutes=5),
)

FuzzyCron.add_job(
    func=persona_reminder,
    trigger=persona_schedule,
    name="persona_reminder",
    tags={"productivity", "persona"},
    pass_context=True,
)


# ---------------------------------------------------------------------------
# 6. Run the async execution loop
# ---------------------------------------------------------------------------
async def main() -> None:
    loop = ExecutionLoop(
        midnight_recalibration=True,  # refresh solar events at local midnight
        max_concurrency=4,  # max 4 jobs in parallel
        default_job_timeout=timedelta(minutes=2),
        default_condition_timeout=timedelta(seconds=15),
        history_limit=500,
    )

    logger.info("Starting Zeitwerkzeug solar assistant…")
    logger.info("Registered jobs: %d", len(FuzzyCron.default()))
    for job in FuzzyCron.default().jobs:
        logger.info("  • %s  (tags=%s, trigger=%s)", job.name, job.tags, job.trigger.target)

    # Run for 5 minutes so the example is self-contained.
    # In production you would run without a deadline:
    #     await loop.run()
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    await loop.run(until=deadline)

    logger.info("Shutting down. Execution history (%d records):", len(loop.history))
    for record in loop.history:
        logger.info(
            "  %s | %s | attempt=%s | status=%s | error=%s",
            record.job_name,
            record.scheduled_for.isoformat(),
            record.attempt,
            record.status,
            record.error or "—",
        )


if __name__ == "__main__":
    asyncio.run(main())
