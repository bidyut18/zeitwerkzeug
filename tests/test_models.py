"""Unit tests for persistence data models."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from zeitwerkzeug.persistence.persistant_models import (
    ExecutionRecord,
    JobRecord,
)

UTC = UTC


class FakeRow:
    """Minimal sqlite3.Row-like object."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]


class TestExecutionRecord:
    """ExecutionRecord is the core unit persisted for every job run."""

    def test_roundtrip_to_db_row(self):
        """Serializing must produce the expected SQL column order."""
        original = ExecutionRecord(
            job_name="water_plants",
            status="success",
            attempt=2,
            triggered_at=datetime(2026, 8, 21, 6, 30, tzinfo=UTC),
            started_at=datetime(2026, 8, 21, 6, 30, 5, tzinfo=UTC),
            finished_at=datetime(2026, 8, 21, 6, 30, 8, tzinfo=UTC),
            error_message=None,
            context_data={"cloud_cover": 12, "sun_alt": 5.3},
        )

        row = original.to_db_row()

        assert len(row) == 8
        assert row[0] == "water_plants"
        assert row[1] == "success"
        assert row[2] == 2
        assert row[3] == "2026-08-21T06:30:00+00:00"
        assert row[4] == "2026-08-21T06:30:05+00:00"
        assert row[5] == "2026-08-21T06:30:08+00:00"
        assert row[6] is None
        assert json.loads(row[7]) == {"cloud_cover": 12, "sun_alt": 5.3}

    def test_from_db_row(self):
        """Reconstruction from a row object must restore all fields."""
        row = FakeRow(
            {
                "job_name": "open_blinds",
                "status": "failed",
                "attempt": 3,
                "triggered_at": "2026-08-21T07:00:00+00:00",
                "started_at": "2026-08-21T07:00:02+00:00",
                "finished_at": "2026-08-21T07:00:10+00:00",
                "error_message": "TimeoutError: blinds did not respond",
                "context_data": '{"retry_count": 2}',
            }
        )

        rec = ExecutionRecord.from_db_row(row)

        assert rec.job_name == "open_blinds"
        assert rec.status == "failed"
        assert rec.attempt == 3
        assert rec.triggered_at == datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
        assert rec.started_at == datetime(2026, 8, 21, 7, 0, 2, tzinfo=UTC)
        assert rec.finished_at == datetime(2026, 8, 21, 7, 0, 10, tzinfo=UTC)
        assert rec.error_message == "TimeoutError: blinds did not respond"
        assert rec.context_data == {"retry_count": 2}

    def test_from_db_row_handles_null_timestamps(self):
        row = FakeRow(
            {
                "job_name": "x",
                "status": "pending",
                "attempt": 1,
                "triggered_at": None,
                "started_at": None,
                "finished_at": None,
                "error_message": None,
                "context_data": None,
            }
        )

        rec = ExecutionRecord.from_db_row(row)

        assert rec.triggered_at is None
        assert rec.started_at is None
        assert rec.finished_at is None
        assert rec.context_data == {}

    def test_context_data_with_non_json_native_values_is_serialized_as_string(self):
        when = datetime(2026, 1, 1, tzinfo=UTC)
        rec = ExecutionRecord(
            job_name="x",
            status="success",
            context_data={"when": when},
        )

        row = rec.to_db_row()
        payload = json.loads(row[7])

        assert isinstance(payload["when"], str)

    def test_repr_contains_core_fields(self):
        rec = ExecutionRecord(job_name="x", status="success")

        text = repr(rec)

        assert "x" in text
        assert "success" in text

    def test_empty_context_defaults_to_dict(self):
        rec = ExecutionRecord(job_name="x", status="pending")

        assert rec.context_data == {}


class TestJobRecord:
    """JobRecord stores lightweight metadata about registered jobs."""

    def test_roundtrip_includes_pass_context(self):
        original = JobRecord(
            name="garden-irrigation",
            module="myapp.tasks",
            qualname="water_plants",
            schedule_repr="LazySchedule('sunrise')",
            pass_context=False,
            created_at=datetime(2026, 8, 21, 5, 0, tzinfo=UTC),
        )

        row = original.to_db_row()

        assert len(row) == 6
        assert row[0] == "garden-irrigation"
        assert row[1] == "myapp.tasks"
        assert row[2] == "water_plants"
        assert row[3] == "LazySchedule('sunrise')"
        assert row[4] == 0
        assert row[5] == "2026-08-21T05:00:00+00:00"

    def test_pass_context_defaults_to_true(self):
        rec = JobRecord(
            name="x",
            module="m",
            qualname="q",
            schedule_repr="s",
        )

        row = rec.to_db_row()

        assert row[4] == 1

    def test_from_db_row(self):
        row = FakeRow(
            {
                "name": "close_blinds",
                "module": "myapp.tasks",
                "qualname": "close_blinds",
                "schedule_repr": "LazySchedule('civil_dusk')",
                "pass_context": 1,
                "created_at": "2026-08-21T19:30:00+00:00",
            }
        )

        rec = JobRecord.from_db_row(row)

        assert rec.name == "close_blinds"
        assert rec.module == "myapp.tasks"
        assert rec.qualname == "close_blinds"
        assert rec.schedule_repr == "LazySchedule('civil_dusk')"
        assert rec.pass_context is True
        assert rec.created_at == datetime(2026, 8, 21, 19, 30, tzinfo=UTC)

    def test_from_db_row_handles_false_pass_context(self):
        row = FakeRow(
            {
                "name": "x",
                "module": "m",
                "qualname": "q",
                "schedule_repr": "s",
                "pass_context": 0,
                "created_at": None,
            }
        )

        rec = JobRecord.from_db_row(row)

        assert rec.pass_context is False
        assert rec.created_at is None

    def test_from_db_row_accepts_sqlite_current_timestamp_format(self):
        row = FakeRow(
            {
                "name": "x",
                "module": "m",
                "qualname": "q",
                "schedule_repr": "s",
                "pass_context": 1,
                "created_at": "2026-08-21 19:30:00+00:00",
            }
        )

        rec = JobRecord.from_db_row(row)

        assert rec.created_at == datetime(2026, 8, 21, 19, 30, tzinfo=UTC)
