"""Unit tests for PersistentExecutionLoop and PersistentFuzzyCron."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from zeitwerkzeug.persistence.persistance_loop import (
    PersistentExecutionLoop,
    PersistentFuzzyCron,
    _func_fqn,
    _LoopRef,
    _wrap_job,
)
from zeitwerkzeug.persistence.persistant_models import (
    ExecutionRecord,
    JobRecord,
)
from zeitwerkzeug.persistence.store import SQLiteStore

UTC = UTC


class FakeCtx:
    """Minimal execution context object."""

    def __init__(
        self,
        triggered_at: datetime | None = None,
        attempt: int = 1,
    ) -> None:
        self.triggered_at = triggered_at or datetime.now(UTC)
        self.attempt = attempt
        self.trigger = "fake-trigger"


class RecordingRegistry:
    """A minimal registry double."""

    def __init__(self) -> None:
        self.registered: dict[str, tuple[Callable[..., Any], Any, bool]] = {}

    def register(
        self,
        func: Callable[..., Any],
        trigger: Any,
        *,
        name: str,
        pass_context: bool = True,
    ) -> str:
        self.registered[name] = (func, trigger, pass_context)
        return name  # <-- return a truthy job handle

    def remove_job(self, job_id: str) -> None:
        self.registered.pop(job_id, None)


def make_job_record(
    name: str,
    *,
    pass_context: bool = True,
    module: str | None = None,
    qualname: str | None = None,
) -> JobRecord:
    return JobRecord(
        name=name,
        module=module or __name__,
        qualname=qualname or name,
        schedule_repr="fake-trigger",
        pass_context=pass_context,
        created_at=datetime.now(UTC),
    )


async def register_meta(
    store: SQLiteStore,
    name: str,
    *,
    pass_context: bool = True,
) -> JobRecord:
    rec = make_job_record(name, pass_context=pass_context)
    await store.register_job(rec)
    return rec


async def drain_registry(cron: PersistentFuzzyCron) -> None:
    """Allow scheduled persistence writes to complete."""
    await asyncio.sleep(0)
    await cron.wait_for_writes()
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# _func_fqn
# ---------------------------------------------------------------------------


class TestFuncFqn:
    def test_regular_function(self):
        def my_func():
            pass

        module, qualname = _func_fqn(my_func)

        assert module == __name__
        assert qualname.endswith(".my_func")

    def test_lambda(self):
        f = lambda: None  # noqa: E731

        module, qualname = _func_fqn(f)

        assert module == __name__
        assert "lambda" in qualname


# ---------------------------------------------------------------------------
# _wrap_job: async wrappers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def tracing_store(tmp_path):
    store = SQLiteStore(tmp_path / "trace.db")
    await store.init()
    yield store
    await store.close()


class TestWrapJobAsync:
    @pytest.mark.asyncio
    async def test_async_success_is_logged(self, tracing_store):
        rec = await register_meta(tracing_store, "my-task")

        async def task(ctx):
            return 42

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            job_record=rec,
        )

        result = await wrapped(FakeCtx())

        assert result == 42

        history = await tracing_store.get_history(job_name="my-task")

        assert len(history) == 1
        assert history[0].status == "success"
        assert history[0].started_at is not None
        assert history[0].finished_at is not None

    @pytest.mark.asyncio
    async def test_async_failure_is_logged(self, tracing_store):
        rec = await register_meta(tracing_store, "fail-task")

        async def task(ctx):
            raise ValueError("boom")

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            job_record=rec,
        )

        with pytest.raises(ValueError, match="boom"):
            await wrapped(FakeCtx())

        history = await tracing_store.get_history(job_name="fail-task")

        assert len(history) == 1
        assert history[0].status == "failed"
        assert "boom" in history[0].error_message

    @pytest.mark.asyncio
    async def test_async_pass_context_false(self, tracing_store):
        rec = await register_meta(tracing_store, "no-ctx")

        async def task():
            return 99

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            pass_context=False,
            job_record=rec,
        )

        result = await wrapped(FakeCtx())

        assert result == 99

        history = await tracing_store.get_history(job_name="no-ctx")
        assert history[0].status == "success"

    @pytest.mark.asyncio
    async def test_async_wrapper_auto_registers_missing_job_metadata(
        self,
        tracing_store,
    ):
        rec = make_job_record("auto-task")

        async def task(ctx):
            return 1

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            job_record=rec,
        )

        result = await wrapped(FakeCtx())

        assert result == 1

        jobs = await tracing_store.list_jobs()
        assert any(job.name == "auto-task" for job in jobs)

        history = await tracing_store.get_history(job_name="auto-task")
        assert history[0].status == "success"

    @pytest.mark.asyncio
    async def test_async_cancellation_is_logged_as_timeout(self, tracing_store):
        rec = await register_meta(tracing_store, "cancel-task")

        started = asyncio.Event()

        async def slow_task(ctx):
            started.set()
            await asyncio.sleep(5)

        wrapped = _wrap_job(
            tracing_store,
            slow_task,
            rec.name,
            job_record=rec,
        )

        task = asyncio.create_task(wrapped(FakeCtx()))

        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        history = await tracing_store.get_history(job_name="cancel-task")

        assert len(history) == 1
        assert history[0].status == "timeout"
        assert history[0].finished_at is not None


# ---------------------------------------------------------------------------
# _wrap_job: sync wrappers
# ---------------------------------------------------------------------------


class TestWrapJobSync:
    @pytest.mark.asyncio
    async def test_sync_success_is_logged(self, tracing_store):
        rec = await register_meta(tracing_store, "sync-task")

        loop_ref = _LoopRef()
        loop_ref.bind()

        def task(ctx):
            return 77

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            loop_ref=loop_ref,
            job_record=rec,
        )

        result = await asyncio.to_thread(wrapped, FakeCtx())

        assert result == 77

        history = await tracing_store.get_history(job_name="sync-task")

        assert len(history) == 1
        assert history[0].status == "success"

    @pytest.mark.asyncio
    async def test_sync_failure_is_logged(self, tracing_store):
        rec = await register_meta(tracing_store, "sync-fail")

        loop_ref = _LoopRef()
        loop_ref.bind()

        def task(ctx):
            raise RuntimeError("sync boom")

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            loop_ref=loop_ref,
            job_record=rec,
        )

        with pytest.raises(RuntimeError, match="sync boom"):
            await asyncio.to_thread(wrapped, FakeCtx())

        history = await tracing_store.get_history(job_name="sync-fail")

        assert len(history) == 1
        assert history[0].status == "failed"
        assert "sync boom" in history[0].error_message

    @pytest.mark.asyncio
    async def test_sync_pass_context_false(self, tracing_store):
        rec = await register_meta(tracing_store, "sync-no-ctx")

        loop_ref = _LoopRef()
        loop_ref.bind()

        def task():
            return 88

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            pass_context=False,
            loop_ref=loop_ref,
            job_record=rec,
        )

        result = await asyncio.to_thread(wrapped, FakeCtx())

        assert result == 88

        history = await tracing_store.get_history(job_name="sync-no-ctx")
        assert history[0].status == "success"

    @pytest.mark.asyncio
    async def test_sync_wrapper_refuses_to_run_on_event_loop_thread(
        self,
        tracing_store,
    ):
        rec = await register_meta(tracing_store, "sync-loop-thread")

        loop_ref = _LoopRef()
        loop_ref.bind()

        def task(ctx):
            return 1

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            loop_ref=loop_ref,
            job_record=rec,
        )

        with pytest.raises(RuntimeError, match="event loop thread"):
            wrapped(FakeCtx())

    @pytest.mark.asyncio
    async def test_sync_wrapper_requires_bound_loop(self, tracing_store):
        rec = await register_meta(tracing_store, "sync-unbound")

        loop_ref = _LoopRef()

        def task(ctx):
            return 1

        wrapped = _wrap_job(
            tracing_store,
            task,
            rec.name,
            loop_ref=loop_ref,
            job_record=rec,
        )

        with pytest.raises(RuntimeError, match="bound event loop"):
            await asyncio.to_thread(wrapped, FakeCtx())


# ---------------------------------------------------------------------------
# PersistentFuzzyCron
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pcron(tmp_path):
    store = SQLiteStore(tmp_path / "cron.db")
    await store.init()

    inner = RecordingRegistry()
    cron = PersistentFuzzyCron(store, registry=inner)

    yield cron, store, inner

    await cron.wait_for_writes()
    await store.close()


class TestPersistentFuzzyCron:
    @pytest.mark.asyncio
    async def test_register_persists_metadata(self, pcron):
        cron, store, inner = pcron

        async def my_job(ctx):
            pass

        cron.register(my_job, trigger=object(), name="my-job")

        await drain_registry(cron)

        jobs = await store.list_jobs()

        assert len(jobs) == 1
        assert jobs[0].name == "my-job"
        assert jobs[0].module == __name__
        assert jobs[0].pass_context is True

        assert "my-job" in inner.registered
        assert cron.get_job("my-job") is my_job

    @pytest.mark.asyncio
    async def test_add_job_alias(self, pcron):
        cron, store, _ = pcron

        async def job(ctx):
            pass

        cron.add_job(job, trigger=object(), name="alias-job")

        await drain_registry(cron)

        jobs = await store.list_jobs()

        assert any(job.name == "alias-job" for job in jobs)

    @pytest.mark.asyncio
    async def test_remove_job(self, pcron):
        cron, store, inner = pcron

        async def job(ctx):
            pass

        cron.register(job, trigger=object(), name="rm-job")
        await drain_registry(cron)

        cron.remove_job("rm-job")
        await drain_registry(cron)

        jobs = await store.list_jobs()

        assert not any(job.name == "rm-job" for job in jobs)
        assert "rm-job" not in inner.registered
        assert cron.get_job("rm-job") is None

    @pytest.mark.asyncio
    async def test_get_job_returns_original(self, pcron):
        cron, _, _ = pcron

        async def original(ctx):
            pass

        cron.register(original, trigger=object(), name="orig")

        assert cron.get_job("orig") is original

    @pytest.mark.asyncio
    async def test_registered_wrapper_persists_execution(self, pcron):
        cron, store, inner = pcron

        async def task(ctx):
            return "ok"

        cron.register(task, trigger=object(), name="trace-run")
        await drain_registry(cron)

        wrapped, _, _ = inner.registered["trace-run"]

        result = await wrapped(FakeCtx())

        assert result == "ok"

        history = await store.get_history(job_name="trace-run")

        assert len(history) == 1
        assert history[0].status == "success"

    @pytest.mark.asyncio
    async def test_restore_jobs(self, pcron):
        cron, store, _ = pcron

        async def job_a(ctx):
            pass

        cron.register(job_a, trigger=object(), name="restored")
        await drain_registry(cron)

        fresh_inner = RecordingRegistry()
        fresh_cron = PersistentFuzzyCron(store, registry=fresh_inner)

        async def stub(ctx):
            pass

        def loader(record: JobRecord):
            return stub, object()

        await fresh_cron.restore_jobs(loader)
        await drain_registry(fresh_cron)

        assert fresh_cron.get_job("restored") is not None
        assert "restored" in fresh_inner.registered

    @pytest.mark.asyncio
    async def test_restore_jobs_respects_pass_context_false(self, pcron):
        cron, store, _ = pcron

        async def original(ctx):
            pass

        cron.register(
            original,
            trigger=object(),
            name="ctx-false",
            pass_context=False,
        )
        await drain_registry(cron)

        fresh_inner = RecordingRegistry()
        fresh_cron = PersistentFuzzyCron(store, registry=fresh_inner)

        missing = object()
        calls = []

        async def stub(ctx=missing):
            calls.append(ctx)

        def loader(record: JobRecord):
            return stub, object()

        await fresh_cron.restore_jobs(loader)
        await drain_registry(fresh_cron)

        wrapped, _, _ = fresh_inner.registered["ctx-false"]

        await wrapped(FakeCtx())

        assert len(calls) == 1
        assert calls[0] is missing

    @pytest.mark.asyncio
    async def test_restore_jobs_respects_pass_context_true(self, pcron):
        cron, store, _ = pcron

        async def original(ctx):
            pass

        cron.register(
            original,
            trigger=object(),
            name="ctx-true",
            pass_context=True,
        )
        await drain_registry(cron)

        fresh_inner = RecordingRegistry()
        fresh_cron = PersistentFuzzyCron(store, registry=fresh_inner)

        calls = []

        async def stub(ctx=None):
            calls.append(ctx)

        def loader(record: JobRecord):
            return stub, object()

        await fresh_cron.restore_jobs(loader)
        await drain_registry(fresh_cron)

        wrapped, _, _ = fresh_inner.registered["ctx-true"]
        ctx = FakeCtx()

        await wrapped(ctx)

        assert len(calls) == 1
        assert calls[0] is ctx

    @pytest.mark.asyncio
    async def test_restore_jobs_is_idempotent(self, pcron):
        cron, store, _ = pcron

        async def original(ctx):
            pass

        cron.register(original, trigger=object(), name="once")
        await drain_registry(cron)

        fresh_inner = RecordingRegistry()
        fresh_cron = PersistentFuzzyCron(store, registry=fresh_inner)

        async def stub(ctx):
            pass

        def loader(record: JobRecord):
            return stub, object()

        await fresh_cron.restore_jobs(loader)
        await fresh_cron.restore_jobs(loader)

        assert "once" in fresh_inner.registered


# ---------------------------------------------------------------------------
# PersistentExecutionLoop
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ploop(tmp_path):
    loop = PersistentExecutionLoop(
        db_path=tmp_path / "loop.db",
        max_concurrency=2,
        default_job_timeout=timedelta(seconds=10),
    )
    await loop.init()
    yield loop
    await loop.close()


class TestPersistentExecutionLoop:
    @pytest.mark.asyncio
    async def test_init_creates_db(self, tmp_path):
        db = tmp_path / "init.db"

        assert not db.exists()

        loop = PersistentExecutionLoop(db_path=db)
        await loop.init()

        assert db.exists()

        await loop.close()

    @pytest.mark.asyncio
    async def test_init_restores_history(self, tmp_path):
        db = tmp_path / "restore-history.db"

        store = SQLiteStore(db)
        await store.init()
        await store.register_job(make_job_record("old"))
        await store.log_execution(
            ExecutionRecord(
                job_name="old",
                status="success",
                triggered_at=datetime.now(UTC),
            )
        )
        await store.close()

        loop = PersistentExecutionLoop(db_path=db)
        await loop.init()

        try:
            assert any(getattr(item, "job_name", None) == "old" for item in loop.history)
        finally:
            await loop.close()

    @pytest.mark.asyncio
    async def test_run_without_init_raises(self, tmp_path):
        loop = PersistentExecutionLoop(db_path=tmp_path / "no-init.db")

        with pytest.raises(RuntimeError, match="init"):
            await loop.run()

        await loop.close()

    @pytest.mark.asyncio
    async def test_run_and_stop(self, ploop):
        async def background():
            await ploop.run()

        task = asyncio.create_task(background())

        await asyncio.sleep(0.05)
        await ploop.stop()

        await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_db_stats(self, tmp_path):
        db = tmp_path / "stats.db"

        store = SQLiteStore(db)
        await store.init()
        await store.register_job(make_job_record("j"))
        await store.log_execution(ExecutionRecord(job_name="j", status="success"))
        await store.log_execution(ExecutionRecord(job_name="j", status="failed"))
        await store.close()

        loop = PersistentExecutionLoop(db_path=db)
        await loop.init()

        try:
            stats = await loop.db_stats()

            assert stats["total_executions"] == 2
            assert stats["successful"] == 1
            assert stats["failed"] == 1
            assert stats["registered_jobs"] == 1
            assert stats["db_path"] == str(db)
        finally:
            await loop.close()

    @pytest.mark.asyncio
    async def test_prune_on_shutdown(self, tmp_path):
        db = tmp_path / "prune.db"

        store = SQLiteStore(db)
        await store.init()
        await store.register_job(make_job_record("x"))
        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=datetime.now(UTC) - timedelta(days=5),
            )
        )
        await store.close()

        loop = PersistentExecutionLoop(
            db_path=db,
            prune_older_than=timedelta(days=1),
        )
        await loop.init()

        await loop.run(until=datetime.now(UTC) + timedelta(milliseconds=100))
        await loop.close()

        check_store = SQLiteStore(db)
        await check_store.init()

        remaining = await check_store.get_history()

        assert len(remaining) == 0

        await check_store.close()

    @pytest.mark.asyncio
    async def test_no_prune_when_disabled(self, tmp_path):
        db = tmp_path / "no-prune.db"

        store = SQLiteStore(db)
        await store.init()
        await store.register_job(make_job_record("x"))
        await store.log_execution(
            ExecutionRecord(
                job_name="x",
                status="success",
                triggered_at=datetime.now(UTC) - timedelta(days=5),
            )
        )
        await store.close()

        loop = PersistentExecutionLoop(
            db_path=db,
            prune_older_than=None,
        )
        await loop.init()

        await loop.run(until=datetime.now(UTC) + timedelta(milliseconds=100))
        await loop.close()

        check_store = SQLiteStore(db)
        await check_store.init()

        remaining = await check_store.get_history()

        assert len(remaining) == 1

        await check_store.close()

    @pytest.mark.asyncio
    async def test_registry_property(self, ploop):
        assert isinstance(ploop.registry, PersistentFuzzyCron)

    @pytest.mark.asyncio
    async def test_pending_writes_are_flushed_during_init(self, tmp_path, mock_trigger):
        loop = PersistentExecutionLoop(db_path=tmp_path / "pending.db")

        async def job(ctx):
            pass

        # Use a valid mock trigger instead of object() to satisfy FuzzyCron validation
        trigger = mock_trigger(next_at=datetime.now(UTC) + timedelta(hours=1))
        loop.registry.add_job(job, trigger=trigger, name="pre-init")

        await loop.init()

        try:
            stats = await loop.db_stats()
            assert stats["registered_jobs"] == 1
        finally:
            await loop.close()

    @pytest.mark.asyncio
    async def test_external_registry_is_supported(self, tmp_path):
        store = SQLiteStore(tmp_path / "external.db")
        registry = PersistentFuzzyCron(store)

        loop = PersistentExecutionLoop(registry=registry)

        await loop.init()

        try:
            assert loop.registry is registry

            stats = await loop.db_stats()
            assert stats["db_path"] == str(store.db_path)
        finally:
            await loop.close()
