"""Fuzzy cron registry."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar

from zeitwerkzeug.context.scheduler import LazySchedule
from zeitwerkzeug.exceptions import JobError


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Registered job definition."""

    id: uuid.UUID
    func: Callable[..., object]
    trigger: LazySchedule
    name: str
    tags: frozenset[str] = frozenset()
    args: tuple[object, ...] = ()
    kwargs: Mapping[str, object] = field(default_factory=dict)
    pass_context: bool = False

    # Production controls.
    job_timeout: timedelta | None = None
    condition_timeout: timedelta | None = None
    max_latency: timedelta | None = None


class FuzzyCron:
    """Job registry.

    The class methods operate on a default global registry so that the public
    API can match the desired fluent style:

        FuzzyCron.add_job(func, trigger)

    For applications that need isolation, instantiate a dedicated registry:

        cron = FuzzyCron()
        cron.register(func, trigger)
    """

    _default: ClassVar[FuzzyCron | None] = None

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, JobSpec] = {}

    @classmethod
    def default(cls) -> FuzzyCron:
        """Return the process-wide default registry."""
        if cls._default is None:
            cls._default = cls()
        return cls._default

    @classmethod
    def add_job(
        cls,
        func: Callable[..., object],
        trigger: LazySchedule,
        *,
        name: str | None = None,
        tags: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
        args: tuple[object, ...] = (),
        kwargs: Mapping[str, object] | None = None,
        pass_context: bool = False,
        job_timeout: timedelta | None = None,
        condition_timeout: timedelta | None = None,
        max_latency: timedelta | None = None,
    ) -> JobSpec:
        """Add a job to the default registry."""
        return cls.default().register(
            func,
            trigger,
            name=name,
            tags=tags,
            args=args,
            kwargs=kwargs,
            pass_context=pass_context,
            job_timeout=job_timeout,
            condition_timeout=condition_timeout,
            max_latency=max_latency,
        )

    @classmethod
    def remove_job(cls, job_id: uuid.UUID) -> None:
        """Remove a job from the default registry."""
        cls.default().remove(job_id)

    def register(
        self,
        func: Callable[..., object],
        trigger: LazySchedule,
        *,
        name: str | None = None,
        tags: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
        args: tuple[object, ...] = (),
        kwargs: Mapping[str, object] | None = None,
        pass_context: bool = False,
        job_timeout: timedelta | None = None,
        condition_timeout: timedelta | None = None,
        max_latency: timedelta | None = None,
    ) -> JobSpec:
        """Register a job on this registry."""
        if not isinstance(trigger, LazySchedule):
            raise JobError("trigger must be a LazySchedule instance.")

        job_id = uuid.uuid4()

        resolved_name = name
        if not resolved_name:
            try:
                resolved_name = func.__name__
            except AttributeError:
                resolved_name = f"job-{job_id.hex[:8]}"

        if tags is None:
            tag_set: frozenset[str] = frozenset()
        elif isinstance(tags, frozenset):
            tag_set = tags
        else:
            tag_set = frozenset(tags)

        job = JobSpec(
            id=job_id,
            func=func,
            trigger=trigger,
            name=resolved_name,
            tags=tag_set,
            args=args,
            kwargs=dict(kwargs) if kwargs is not None else {},
            pass_context=pass_context,
            job_timeout=job_timeout,
            condition_timeout=condition_timeout,
            max_latency=max_latency,
        )

        self._jobs[job_id] = job
        return job

    def remove(self, job_id: uuid.UUID) -> None:
        """Remove a job from this registry."""
        self._jobs.pop(job_id, None)

    def get_job(self, job_id: uuid.UUID) -> JobSpec | None:
        """Return a job by id."""
        return self._jobs.get(job_id)

    def clear(self) -> None:
        """Remove all jobs from this registry."""
        self._jobs.clear()

    @property
    def jobs(self) -> tuple[JobSpec, ...]:
        """Return all registered jobs."""
        return tuple(self._jobs.values())

    def __len__(self) -> int:
        return len(self._jobs)

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._jobs