"""Contextual scheduling primitives."""

from zeitwerkzeug.context.base_hooks import All, Not, SunAltitudeAbove, TimeWindow
from zeitwerkzeug.context.scheduler import (
    FailPolicy,
    LazySchedule,
    ScheduleBuilder,
    schedule,
)

__all__ = [
    "All",
    "FailPolicy",
    "LazySchedule",
    "Not",
    "ScheduleBuilder",
    "SunAltitudeAbove",
    "TimeWindow",
    "schedule",
]
