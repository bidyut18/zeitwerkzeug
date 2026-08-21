#!/usr/bin/env python3
"""Persistent smart-garden scheduler — drop-in replacement example.

This is the "Smart Garden Irrigation" example from the README, ported to
use ``PersistentExecutionLoop``. Run it twice and notice that history
survives across restarts.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from zeitwerkzeug import Location, ScheduleBuilder
from zeitwerkzeug.persistence import PersistentExecutionLoop

UTC = UTC

# Osaka, Japan
LOCATION = Location(lat=34.6937, lon=135.5020, timezone="Asia/Tokyo")


def water_plants(ctx):
    print(f"💧 Watering garden at {ctx.triggered_at} (attempt {ctx.attempt})")


async def main():
    # 1. Build the persistent loop
    loop = PersistentExecutionLoop(
        db_path="example/garden.db",
        max_concurrency=2,
        default_job_timeout=timedelta(seconds=30),
        prune_older_than=timedelta(days=7),  # keep a week of history
    )

    # Create tables, heal interrupted executions, restore history.
    await loop.init()

    # 2. Build schedule
    schedule = ScheduleBuilder()
    trigger = schedule.at("sunset", location=LOCATION).on_fail(
        retry_interval=timedelta(minutes=15),
        max_attempts=3,
        limit=timedelta(hours=2),
    )

    # 3. Register job via the persistent registry
    loop.registry.add_job(
        water_plants,
        trigger=trigger,
        name="garden-irrigation",
    )

    # Recommended: make sure the job metadata write is flushed.
    await loop.registry.flush_pending_writes()

    # 4. Show restored history from previous runs
    print("📜 Restored history:")
    for r in loop.history[-5:]:
        print(f"   {r.job_name:20} | {r.status:15} | attempt={r.attempt}")

    # 5. Run for 60 seconds in this demo
    print("🌱 Garden irrigation daemon started (ctrl-c to stop)")

    until = datetime.now(UTC) + timedelta(seconds=60)

    try:
        await loop.run(until=until)
    finally:
        # 6. Diagnostics
        stats = await loop.db_stats()
        print(f"\n📊 Stats: {stats}")
        await loop.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
