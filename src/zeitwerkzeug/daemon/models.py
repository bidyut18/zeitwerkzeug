"""Data models for jobs, queue entries, and execution records."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import NamedTuple

from zeitwerkzeug.context.scheduler import LazySchedule


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Registered job definition."""

    id: uuid.UUID
    func: Callable[..., object]
    trigger: LazySchedule
    name: str
    tags: frozenset[str] = frozenset()
    args: tuple[object, ...] = ()
    kwargs: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    pass_context: bool = False

    # Production controls.
    job_timeout: timedelta | None = None
    condition_timeout: timedelta | None = None
    max_latency: timedelta | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """A single historical execution result."""

    job_id: uuid.UUID
    job_name: str
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime
    attempt: int
    status: str
    error: str | None = None

    @property
    def duration(self) -> timedelta:
        """Total time spent evaluating and executing."""
        return self.finished_at - self.started_at


class QueueEntry(NamedTuple):
    """A single item in the execution heap.

    Fields are ordered so that heap comparison works correctly.
    """

    when: datetime
    generation: int
    job_id: uuid.UUID
    attempt: int
