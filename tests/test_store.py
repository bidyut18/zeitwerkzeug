"""Unit tests for SQLiteStore."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from zeitwerkzeug.persistence.persistant_models import (
    ExecutionRecord,
    JobRecord,
)
from zeitwerkzeug.persistence.store import SQLiteStore

UTC = UTC


async def add_job(
    store: SQLiteStore,
    name: str,
    *,
    pass_context: bool = True,
) -> JobRecord:
    """Create and persist a job record.

    The executions table has a foreign key to jobs(name), so execution
    tests must register the job first.
    """
    rec = JobRecord(
        name=name,
        module=__name__,
        qualname=name,
        schedule_repr="fake-trigger",
        pass_context=pass_context,
        created_at=datetime.now(UTC),
    )
    await store.register_job(rec)
    return rec


@pytest_asyncio.fixture
async def store(tmp_path):
    """Fresh store for every test."""
    db = tmp_path / "test.db"
    s = SQLiteStore(db)
    await s.init()
    yield s
    await s.close()


class TestInit:
    """Database creation and migration behavior."""

    @pytest.mark.asyncio
    async def test_creates_core_tables(self, tmp_path):
        db = tmp_path / "fresh.db"
        s = SQLiteStore(db)

        await s.init()

        rows = await s._fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row["name"] for row in rows}

        assert "jobs" in names
        assert "executions" in names
        assert "meta" in names

        await s.close()

    @pytest.mark.asyncio
    async def test_jobs_table_has_pass_context_column(self, tmp_path):
        db = tmp_path / "pass_context.db"
        s = SQLiteStore(db)

        await s.init()

        columns = await s._fetch_all("PRAGMA table_info(jobs)")
        column_names = {row["name"] for row in columns}

        assert "pass_context" in column_names

        await s.close()

    @pytest.mark.asyncio
    async def test_schema_version_is_set(self, tmp_path):
        db = tmp_path / "version.db"
        s = SQLiteStore(db)

        await s.init()

        version = await s._meta_get("schema_version")

        assert version is not None
        assert int(version) >= 1

        await s.close()

    @pytest.mark.asyncio
    async def test_idempotent_init(self, tmp_path):
        """Calling init() twice must not crash."""
        db = tmp_path / "idempotent.db"
        s = SQLiteStore(db)

        await s.init()
        await s.init()

        version = await s._meta_get("schema_version")
        assert int(version) >= 1

        await s.close()

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, tmp_path):
        db = tmp_path / "wal.db"
        s = SQLiteStore(db)

        await s.init()

        row = await s._fetch_one("PRAGMA journal_mode")

        assert row is not None
        assert row[0].upper() == "WAL"

        await s.close()

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self, tmp_path):
        db = tmp_path / "fk.db"
        s = SQLiteStore(db)

        await s.init()

        row = await s._fetch_one("PRAGMA foreign_keys")

        assert row is not None
        assert row[0] == 1

        await s.close()


class TestJobRegistry:
    """CRUD for job metadata."""

    @pytest.mark.asyncio
    async def test_register_and_list(self, store):
        rec = JobRecord(
            name="job-a",
            module="mod",
            qualname="fn",
            schedule_repr="at sunrise",
            pass_context=True,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        await store.register_job(rec)

        jobs = await store.list_jobs()

        assert len(jobs) == 1
        assert jobs[0].name == "job-a"
        assert jobs[0].pass_context is True

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_job(self, store):
        """Registering the same name twice must update, not duplicate."""
        rec1 = JobRecord(
            name="dup",
            module="m1",
            qualname="f1",
            schedule_repr="s1",
            pass_context=True,
        )
        rec2 = JobRecord(
            name="dup",
            module="m2",
            qualname="f2",
            schedule_repr="s2",
            pass_context=False,
        )

        await store.register_job(rec1)
        await store.register_job(rec2)

        jobs = await store.list_jobs()

        assert len(jobs) == 1
        assert jobs[0].module == "m2"
        assert jobs[0].qualname == "f2"
        assert jobs[0].schedule_repr == "s2"
        assert jobs[0].pass_context is False

    @pytest.mark.asyncio
    async def test_unregister(self, store):
        await add_job(store, "gone")

        await store.unregister_job("gone")

        jobs = await store.list_jobs()
        assert len(jobs) == 0

    @pytest.mark.asyncio
    async def test_cascade_delete_executions(self, store):
        """Deleting a job should cascade to its execution history."""
        await add_job(store, "cascade")

        await store.log_execution(ExecutionRecord(job_name="cascade", status="success"))

        await store.unregister_job("cascade")

        history = await store.get_history(job_name="cascade")
        assert len(history) == 0


class TestExecutionLogging:
    """Writing and querying execution records."""

    @pytest.mark.asyncio
    async def test_log_and_retrieve(self, store):
        await add_job(store, "task")

        rec = ExecutionRecord(
            job_name="task",
            status="success",
            attempt=1,
            triggered_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
        )

        execution_id = await store.log_execution(rec)

        assert isinstance(execution_id, int)
        assert execution_id > 0

        history = await store.get_history(job_name="task")

        assert len(history) == 1
        assert history[0].status == "success"

    @pytest.mark.asyncio
    async def test_foreign_key_requires_job_row(self, store):
        """Execution insert must fail if the job is unknown."""
        with pytest.raises(sqlite3.IntegrityError):
            await store.log_execution(ExecutionRecord(job_name="missing-job", status="running"))

    @pytest.mark.asyncio
    async def test_update_execution(self, store):
        await add_job(store, "task")

        execution_id = await store.log_execution(ExecutionRecord(job_name="task", status="running"))

        finished_at = datetime(2026, 8, 21, 6, 1, tzinfo=UTC)

        await store.update_execution(
            execution_id,
            status="failed",
            finished_at=finished_at,
        )

        history = await store.get_history(job_name="task")

        assert history[0].status == "failed"
        assert history[0].finished_at == finished_at

    @pytest.mark.asyncio
    async def test_update_noop(self, store):
        """Calling update_execution with no fields must be a no-op."""
        await add_job(store, "task")

        execution_id = await store.log_execution(ExecutionRecord(job_name="task", status="running"))

        await store.update_execution(execution_id)

        history = await store.get_history(job_name="task")

        assert history[0].status == "running"

    @pytest.mark.asyncio
    async def test_get_history_filters(self, store):
        await add_job(store, "job-a")
        await add_job(store, "job-b")

        now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)

        await store.log_execution(
            ExecutionRecord(
                job_name="job-a",
                status="success",
                triggered_at=now - timedelta(hours=2),
            )
        )
        await store.log_execution(
            ExecutionRecord(
                job_name="job-a",
                status="success",
                triggered_at=now - timedelta(hours=1),
            )
        )
        await store.log_execution(
            ExecutionRecord(
                job_name="job-a",
                status="failed",
                triggered_at=now - timedelta(minutes=30),
            )
        )
        await store.log_execution(
            ExecutionRecord(
                job_name="job-b",
                status="success",
                triggered_at=now,
            )
        )

        assert len(await store.get_history(job_name="job-a")) == 3
        assert len(await store.get_history(job_name="job-a", status="success")) == 2
        assert len(await store.get_history(status="failed")) == 1

        since = now - timedelta(minutes=45)
        assert len(await store.get_history(since=since)) == 2

        until = now - timedelta(minutes=45)
        assert len(await store.get_history(until=until)) == 2

    @pytest.mark.asyncio
    async def test_get_history_ordering_newest_first(self, store):
        await add_job(store, "task")

        base = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)

        for i in range(3):
            await store.log_execution(
                ExecutionRecord(
                    job_name="task",
                    status="success",
                    triggered_at=base + timedelta(hours=i),
                )
            )

        history = await store.get_history(job_name="task")

        assert history[0].triggered_at == base + timedelta(hours=2)
        assert history[1].triggered_at == base + timedelta(hours=1)
        assert history[2].triggered_at == base

    @pytest.mark.asyncio
    async def test_get_history_ties_are_ordered_by_id_desc(self, store):
        await add_job(store, "task")

        ts = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)

        for message in ("first", "second", "third"):
            await store.log_execution(
                ExecutionRecord(
                    job_name="task",
                    status="success",
                    triggered_at=ts,
                    error_message=message,
                )
            )

        history = await store.get_history(job_name="task")

        assert [record.error_message for record in history] == [
            "third",
            "second",
            "first",
        ]

    @pytest.mark.asyncio
    async def test_get_last_execution(self, store):
        await add_job(store, "x")

        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="failed",
                triggered_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )

        last = await store.get_last_execution("x")

        assert last is not None
        assert last.status == "failed"

    @pytest.mark.asyncio
    async def test_execution_count(self, store):
        await add_job(store, "x")

        for _ in range(5):
            await store.log_execution(ExecutionRecord(job_name="x", status="success"))

        for _ in range(3):
            await store.log_execution(ExecutionRecord(job_name="x", status="failed"))

        assert await store.execution_count() == 8
        assert await store.execution_count(job_name="x", status="success") == 5
        assert await store.execution_count(job_name="x", status="failed") == 3


class TestCrashRecovery:
    """Crash recovery for executions left in running state."""

    @pytest.mark.asyncio
    async def test_heal_interrupted_marks_running_as_failed(self, store):
        await add_job(store, "crash-test")

        await store.log_execution(
            ExecutionRecord(
                job_name="crash-test",
                status="running",
                started_at=datetime(2026, 8, 21, 5, 0, tzinfo=UTC),
            )
        )
        await store.log_execution(
            ExecutionRecord(
                job_name="crash-test",
                status="success",
                started_at=datetime(2026, 8, 21, 5, 1, tzinfo=UTC),
            )
        )

        healed = await store.heal_interrupted()

        assert healed == 1

        failed = await store.get_history(
            job_name="crash-test",
            status="failed",
        )

        assert len(failed) == 1
        assert failed[0].finished_at is not None
        assert "Daemon restarted" in failed[0].error_message

        success = await store.get_history(
            job_name="crash-test",
            status="success",
        )
        assert len(success) == 1


class TestMaintenance:
    """Pruning and vacuuming."""

    @pytest.mark.asyncio
    async def test_prune_deletes_old_records(self, store):
        await add_job(store, "x")

        now = datetime.now(UTC)
        old = now - timedelta(days=40)
        recent = now - timedelta(days=5)

        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=old,
            )
        )
        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=recent,
            )
        )

        deleted = await store.prune(timedelta(days=30))

        assert deleted == 1

        remaining = await store.get_history()

        assert len(remaining) == 1
        assert remaining[0].triggered_at == recent

    @pytest.mark.asyncio
    async def test_prune_uses_started_at_when_triggered_at_is_missing(self, store):
        await add_job(store, "x")

        old = datetime.now(UTC) - timedelta(days=40)

        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=None,
                started_at=old,
            )
        )

        deleted = await store.prune(timedelta(days=30))

        assert deleted == 1

        remaining = await store.get_history()
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_prune_zero_when_nothing_old(self, store):
        await add_job(store, "x")

        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=datetime.now(UTC),
            )
        )

        deleted = await store.prune(timedelta(days=30))

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_vacuum_does_not_crash(self, store):
        await store.vacuum()
