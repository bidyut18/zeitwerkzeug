"""Data models for zeitwerkzeug persistence layer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _iso_utc(value: datetime | None) -> str | None:
    """Serialize a datetime as an ISO-8601 UTC string."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string from SQLite."""
    if not value:
        return None

    # SQLite CURRENT_TIMESTAMP uses a space separator.
    # datetime.fromisoformat() is safer with "T".
    value = value.replace(" ", "T", 1)

    # Be tolerant of trailing Z.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """Immutable snapshot of a single job execution.

    Mirrors the public shape of ``ExecutionLoop.history`` items so that
    persisted records can be dropped back into the in-memory list without
    conversion.
    """

    job_name: str
    status: str  # pending | running | success | failed | skipped | timeout
    attempt: int = 1
    triggered_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    context_data: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_db_row(self) -> tuple[Any, ...]:
        """Serialize to a tuple matching the ``executions`` table column order."""
        return (
            self.job_name,
            self.status,
            self.attempt,
            _iso_utc(self.triggered_at),
            _iso_utc(self.started_at),
            _iso_utc(self.finished_at),
            self.error_message,
            json.dumps(self.context_data or {}, default=str),
        )

    @classmethod
    def from_db_row(cls, row: Any) -> ExecutionRecord:
        """Reconstruct from a ``sqlite3.Row``."""
        return cls(
            job_name=row["job_name"],
            status=row["status"],
            attempt=row["attempt"],
            triggered_at=_parse_dt(row["triggered_at"]),
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            error_message=row["error_message"],
            context_data=json.loads(row["context_data"] or "{}"),
        )

    def __repr__(self) -> str:
        ts = self.triggered_at.isoformat(timespec="minutes") if self.triggered_at else "—"
        return (
            f"ExecutionRecord({self.job_name!r}, {self.status}, "
            f"attempt={self.attempt}, triggered_at={ts})"
        )


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Lightweight metadata about a registered job.

    We intentionally do **not** pickle the trigger/condition objects.
    Instead we store enough metadata so that a user-supplied *loader*
    can re-hydrate the schedule on startup.
    """

    name: str
    module: str
    qualname: str
    schedule_repr: str
    pass_context: bool = True
    created_at: datetime | None = None

    def to_db_row(self) -> tuple[Any, ...]:
        return (
            self.name,
            self.module,
            self.qualname,
            self.schedule_repr,
            int(self.pass_context),
            _iso_utc(self.created_at),
        )

    @classmethod
    def from_db_row(cls, row: Any) -> JobRecord:
        return cls(
            name=row["name"],
            module=row["module"],
            qualname=row["qualname"],
            schedule_repr=row["schedule_repr"],
            pass_context=bool(row["pass_context"]) if row["pass_context"] is not None else True,
            created_at=_parse_dt(row["created_at"]),
        )
