"""Central exception hierarchy for Zeitwerkzeug."""

from __future__ import annotations


class ZeitwerkzeugError(Exception):
    """Base exception for all Zeitwerkzeug errors."""


class LocationError(ZeitwerkzeugError, ValueError):
    """Raised when a location is invalid or unusable."""


class SolarEventNotFoundError(ZeitwerkzeugError):
    """Raised when a solar event cannot be found for a date/location."""


class ScheduleError(ZeitwerkzeugError):
    """Raised when a schedule cannot be resolved."""


class ConditionEvaluationError(ZeitwerkzeugError):
    """Raised when a condition plugin fails during evaluation."""


class JobError(ZeitwerkzeugError):
    """Raised when a scheduled job is invalid or fails."""


class PersonaError(ZeitwerkzeugError):
    """Raised when persona parsing or profile resolution fails."""
