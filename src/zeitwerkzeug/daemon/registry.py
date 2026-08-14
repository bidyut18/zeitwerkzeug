"""Fuzzy cron registry."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import ClassVar

from zeitwerkzeug.context.scheduler import LazySchedule
from zeitwerkzeug.daemon.models import JobSpec
from zeitwerkzeug.exceptions import JobError


class FuzzyCron:
    """Job registry.

    The class methods operate on a default global registry so that the public
    API can match the desired fluent style::

        FuzzyCron.add_job(func, trigger)

    For applications that need isolation, instantiate a dedicated registry::

        cron = FuzzyCron()
        cron.register(func, trigger)
    """

    _default: ClassVar[FuzzyCron | None] = None

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, JobSpec] = {}
        self._revision: int = 0

    @classmethod
    def default(cls) -> FuzzyCron:
        """Return the process-wide default registry."""
        if cls._default is None:
            cls._default = cls()
        return cls._default

    @classmethod
    def reset_default(cls) -> None:
        """Reset the process-wide default registry singleton."""
        cls._default = None

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

    @staticmethod
    def _is_valid_trigger(trigger: object) -> bool:
        """Verify that *trigger* satisfies the scheduler protocol."""
        required_attrs = (
            "resolve_after",
            "timezone_info",
            "conditions",
            "fail_policy",
        )
        if not all(hasattr(trigger, attr) for attr in required_attrs):
            return False
        return callable(getattr(trigger, "resolve_after", None))

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
        if not self._is_valid_trigger(trigger):
            raise JobError("trigger must satisfy the scheduler protocol.")

        job_id = uuid.uuid4()

        resolved_name = name
        if resolved_name is None:
            try:
                resolved_name = func.__name__
                if resolved_name == "<lambda>":
                    resolved_name = f"job-{job_id.hex[:8]}"
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
            kwargs=MappingProxyType(dict(kwargs)) if kwargs is not None else MappingProxyType({}),
            pass_context=pass_context,
            job_timeout=job_timeout,
            condition_timeout=condition_timeout,
            max_latency=max_latency,
        )

        self._jobs[job_id] = job
        self._revision += 1
        return job

    def remove(self, job_id: uuid.UUID) -> None:
        """Remove a job from this registry."""
        if self._jobs.pop(job_id, None) is not None:
            self._revision += 1

    def get_job(self, job_id: uuid.UUID) -> JobSpec | None:
        """Return a job by id."""
        return self._jobs.get(job_id)

    def clear(self) -> None:
        """Remove all jobs from this registry."""
        if self._jobs:
            self._jobs.clear()
            self._revision += 1

    @property
    def jobs(self) -> tuple[JobSpec, ...]:
        """Return all registered jobs."""
        return tuple(self._jobs.values())

    @property
    def revision(self) -> int:
        """Monotonic counter incremented on every mutating operation."""
        return self._revision

    def __len__(self) -> int:
        return len(self._jobs)

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._jobs
