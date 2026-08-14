"""Zeitwerkzeug public api"""

from logging import NullHandler, getLogger

from . import exceptions
from .astro.constants import Twilight
from .astro.events import SolarAngle, SolarEvent
from .astro.location import Location
from .context.base_hooks import (
    All,
    AlwaysTrue,
    Any,
    Not,
    SunAltitudeAbove,
    TimeWindow,
)
from .context.scheduler import (
    FailPolicy,
    LazySchedule,
    ScheduleBuilder,
    schedule,
)
from .daemon import SystemClock
from .daemon.cron import FuzzyCron, JobSpec
from .daemon.loop import ExecutionLoop
from .integrations.weather import ClearWeather
from .interfaces import ConditionPlugin, ExecutionContext
from .personas.parser import PersonaParser
from .personas.profile import (
    NightShift,
    PersonaProfile,
    StandardWorker,
    TimeBlock,
)

__version__ = "0.0.3"

getLogger("zeitwerkzeug").addHandler(NullHandler())

__version__ = "0.0.3"


def __getattr__(name: str) -> object:
    """Lazily load optional integrations only when requested."""
    if name == "ClearWeather":
        try:
            from zeitwerkzeug.integrations.weather import ClearWeather

            return ClearWeather
        except ImportError as err:
            raise ImportError(
                "The 'ClearWeather' condition requires the 'weather' extra.\n"
                "Install it with: uv pip install 'zeitwerkzeug[weather]'"
            ) from err

    raise AttributeError(f"module 'zeitwerkzeug' has no attribute {name!r}")


__all__ = [
    "All",
    "AlwaysTrue",
    "Any",
    "ClearWeather",
    "ConditionPlugin",
    "ExecutionContext",
    "ExecutionLoop",
    "FailPolicy",
    "FuzzyCron",
    "JobSpec",
    "LazySchedule",
    "Location",
    "NightShift",
    "Not",
    "PersonaParser",
    "PersonaProfile",
    "ScheduleBuilder",
    "SolarAngle",
    "SolarEvent",
    "StandardWorker",
    "SunAltitudeAbove",
    "SystemClock",
    "TimeBlock",
    "TimeWindow",
    "Twilight",
    "exceptions",
    "schedule",
]
