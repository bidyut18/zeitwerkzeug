"""Water the garden 30 minutes after sunrise, but only if it's not raining."""

from zeitwerkzeug import Location
from zeitwerkzeug.context import schedule
from zeitwerkzeug.daemon import ExecutionLoop, FuzzyCron

loc = Location(lat=28.6139, lon=77.2090, timezone="Asia/Kolkata")
cron = FuzzyCron()


def water_plants(ctx):
    print(f"Watering at {ctx.triggered_at}!")


# Sunrise trigger
sunrise = schedule.solar_event("sunrise", location=loc).offset(minutes=30)
cron.register(water_plants, sunrise, name="morning-water")

# Run for one day
loop = ExecutionLoop(registry=cron)
# loop.run(until=...)
