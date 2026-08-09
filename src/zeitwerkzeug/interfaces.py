"""Public protocols and shared execution context.

These protocols are the dependency-inversion boundary for future plugins.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Context passed to condition plugins and optionally to jobs."""

    job_name: str
    scheduled_for: datetime
    triggered_at: datetime
    attempt: int
    trigger: object = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ConditionPlugin(Protocol):
    """A condition that must be true immediately before execution."""

    def evaluate(self, context: ExecutionContext) -> bool | Awaitable[bool]:
        """Return True if the condition is satisfied."""


@runtime_checkable
class Clock(Protocol):
    """Time source abstraction for testability."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""