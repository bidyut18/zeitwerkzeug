"""Persistent execution loop and registry wrappers.

These classes compose around the existing ``ExecutionLoop`` and ``FuzzyCron``
so that history, job metadata, and execution state survive process restarts.

Integration philosophy
----------------------

- ``ExecutionRecord``: every start / finish of a job.
- ``JobRecord``: enough metadata to re-register jobs on startup.

The user is responsible for re-hydrating trigger objects on boot using a
loader callback.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import sqlite3
from collections.abc import Callable, MutableSequence, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from zeitwerkzeug import ExecutionLoop, FuzzyCron

from .persistant_models import ExecutionRecord, JobRecord
from .store import SQLiteStore

logger = logging.getLogger(__name__)

UTC = UTC
_DB_WRITE_TIMEOUT = 10.0


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _func_fqn(func: Callable[..., Any]) -> tuple[str, str]:
    """Return ``(module, qualname)`` for a callable."""
    module = getattr(func, "__module__", "unknown")
    qualname = getattr(
        func,
        "__qualname__",
        getattr(func, "__name__", "unknown"),
    )
    return module, qualname


class _LoopRef:
    """Mutable holder for the event loop that drives the store.

    Async wrappers already run on the loop. Sync wrappers execute in
    worker threads where ``get_running_loop()`` raises, so they need a
    captured reference to schedule store coroutines via
    ``run_coroutine_threadsafe``.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind(self) -> None:
        """Capture the currently running loop, if any."""
        with contextlib.suppress(RuntimeError):
            self.loop = asyncio.get_running_loop()


def _wrap_job(
    store: SQLiteStore,
    func: Callable[..., Any],
    name: str,
    *,
    pass_context: bool = True,
    loop_ref: _LoopRef | None = None,
    job_record: JobRecord | None = None,
) -> Callable[..., Any]:
    """Return a wrapper that persists execution start/finish around ``func``."""
    loop_ref = loop_ref if loop_ref is not None else _LoopRef()
    is_async = inspect.iscoroutinefunction(func)

    def _make_record(ctx: Any) -> ExecutionRecord:
        attempt = getattr(ctx, "attempt", 1)
        try:
            attempt = int(attempt)
        except Exception:
            attempt = 1

        triggered_at = getattr(ctx, "triggered_at", None)
        if triggered_at is None:
            triggered_at = _now_utc()

        try:
            trigger_repr = str(getattr(ctx, "trigger", "unknown"))
        except Exception:
            trigger_repr = "unknown"

        return ExecutionRecord(
            job_name=name,
            status="running",
            attempt=attempt,
            triggered_at=triggered_at,
            started_at=_now_utc(),
            context_data={"trigger": trigger_repr},
        )

    async def _log_start_async(record: ExecutionRecord) -> int:
        """Log execution start.

        If the job metadata row is not present yet, insert it and retry once.
        This avoids a foreign-key race between background job registration
        and immediate execution.
        """
        try:
            return await store.log_execution(record)
        except sqlite3.IntegrityError:
            if job_record is None:
                raise
            await store.register_job(job_record)
            return await store.log_execution(record)

    async def _async_wrapper(ctx: Any) -> Any:
        record = _make_record(ctx)
        execution_id = await _log_start_async(record)

        try:
            result = await func(ctx) if pass_context else await func()

        except asyncio.CancelledError as cancelled:
            try:
                await asyncio.shield(
                    store.update_execution(
                        execution_id,
                        status="timeout",
                        finished_at=_now_utc(),
                        error_message="Job cancelled (timeout or shutdown)",
                    )
                )
            except asyncio.CancelledError:
                logger.exception(
                    "Cancellation persistence was itself cancelled for job %r",
                    name,
                )
            except Exception:
                logger.exception(
                    "Failed to persist cancellation/timeout for job %r",
                    name,
                )

            raise cancelled

        except Exception as exc:
            try:
                await store.update_execution(
                    execution_id,
                    status="failed",
                    finished_at=_now_utc(),
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            except asyncio.CancelledError:
                logger.exception(
                    "Cancellation interrupted failure persistence for job %r",
                    name,
                )
            except Exception:
                logger.exception(
                    "Failed to persist failure for job %r",
                    name,
                )

            raise exc

        try:
            await store.update_execution(
                execution_id,
                status="success",
                finished_at=_now_utc(),
            )
        except asyncio.CancelledError:
            logger.exception(
                "Cancellation interrupted success persistence for job %r",
                name,
            )
            raise
        except Exception:
            logger.exception(
                "Failed to persist success for job %r",
                name,
            )

        return result

    def _sync_wrapper(ctx: Any) -> Any:
        loop = loop_ref.loop

        if loop is None:
            raise RuntimeError(
                f"Job {name!r}: sync-job persistence requires a bound event loop. "
                "Call PersistentExecutionLoop.init()/run() first."
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Good: we are in a worker thread, safe to block.
            pass
        else:
            raise RuntimeError(
                f"Job {name!r}: refusing to run a blocking sync job on the event "
                "loop thread (it would deadlock persistence writes)."
            )

        record = _make_record(ctx)

        def _run(coro: Any) -> Any:
            return asyncio.run_coroutine_threadsafe(
                coro,
                loop,
            ).result(timeout=_DB_WRITE_TIMEOUT)

        try:
            execution_id = _run(store.log_execution(record))
        except sqlite3.IntegrityError:
            if job_record is None:
                raise
            _run(store.register_job(job_record))
            execution_id = _run(store.log_execution(record))

        def _persist(coro: Any) -> None:
            """Best-effort persistence that does not mask the job result."""
            try:
                _run(coro)
            except Exception, asyncio.CancelledError:
                logger.exception(
                    "Failed to persist execution state for job %r",
                    name,
                )

        try:
            result = func(ctx) if pass_context else func()
        except Exception as exc:
            _persist(
                store.update_execution(
                    execution_id,
                    status="failed",
                    finished_at=_now_utc(),
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            )
            raise exc

        _persist(
            store.update_execution(
                execution_id,
                status="success",
                finished_at=_now_utc(),
            )
        )

        return result

    return _async_wrapper if is_async else _sync_wrapper


# ---------------------------------------------------------------------------
# Persistent Registry
# ---------------------------------------------------------------------------


class PersistentFuzzyCron:
    """Drop-in wrapper around ``FuzzyCron`` that persists job metadata."""

    def __init__(
        self,
        store: SQLiteStore,
        registry: FuzzyCron | None = None,
    ) -> None:
        self._store = store
        self._inner = registry if registry is not None else FuzzyCron()
        self._originals: dict[str, Callable[..., Any]] = {}
        self._loop_ref = _LoopRef()
        self._job_ids: dict[str, Any] = {}
        # Tracked background DB writes.
        self._bg_tasks: set[asyncio.Task[Any]] = set()

        # Writes queued because no loop was running or the store was not
        # initialized yet. Flushed by flush_pending_writes().
        self._pending_writes: list[Callable[[], Any]] = []

    # ---------------------------------------------------------------------
    # Background write plumbing
    # ---------------------------------------------------------------------

    def _schedule_write(self, make_coro: Callable[[], Any]) -> None:
        """Run a write as a tracked task; queue it until that's possible."""
        if not self._store.is_initialized:
            self._pending_writes.append(make_coro)
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._pending_writes.append(make_coro)
            return

        try:
            coro = make_coro()
        except Exception:
            logger.exception("Failed to create persistence write coroutine")
            return

        task = loop.create_task(coro)
        self._bg_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._bg_tasks.discard(t)

            if t.cancelled():
                return

            exc = t.exception()
            if exc is not None:
                logger.error(
                    "Background persistence write failed",
                    exc_info=exc,
                )

        task.add_done_callback(_done)

    async def flush_pending_writes(self) -> None:
        """Persist writes that were queued before a loop/store existed."""
        pending, self._pending_writes = self._pending_writes, []

        if not pending:
            return

        coros: list[Any] = []

        for make_coro in pending:
            try:
                coros.append(make_coro())
            except Exception:
                logger.exception("Failed to create pending persistence write")

        if not coros:
            return

        results = await asyncio.gather(*coros, return_exceptions=True)

        for result in results:
            if isinstance(result, asyncio.CancelledError):
                logger.warning("Pending persistence write was cancelled")
            elif isinstance(result, BaseException):
                logger.error(
                    "Pending persistence write failed",
                    exc_info=result,
                )

    async def wait_for_writes(self) -> None:
        """Await all in-flight background writes."""
        tasks = list(self._bg_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def bind_loop(self) -> None:
        self._loop_ref.bind()

    # ---------------------------------------------------------------------
    # Registry API
    # ---------------------------------------------------------------------

    def register(
        self,
        func: Callable[..., Any],
        trigger: Any,
        *,
        name: str,
        pass_context: bool = True,
    ) -> None:
        """Register a job and persist its metadata to SQLite."""
        module, qualname = _func_fqn(func)

        job_record = JobRecord(
            name=name,
            module=module,
            qualname=qualname,
            schedule_repr=repr(trigger),
            pass_context=pass_context,
            created_at=_now_utc(),
        )

        wrapped = _wrap_job(
            self._store,
            func,
            name,
            pass_context=pass_context,
            loop_ref=self._loop_ref,
            job_record=job_record,
        )

        # Register in the real registry first. If that fails, do not persist.
        job_id = self._inner.register(
            wrapped,
            trigger=trigger,
            name=name,
            pass_context=True,
        )

        self._originals[name] = func
        self._job_ids[name] = job_id
        self._loop_ref.bind()

        self._schedule_write(lambda: self._store.register_job(job_record))

    def add_job(
        self,
        func: Callable[..., Any],
        trigger: Any,
        *,
        name: str,
        pass_context: bool = True,
    ) -> None:
        """Alias for ``register()`` to match the README API."""
        self.register(
            func,
            trigger,
            name=name,
            pass_context=pass_context,
        )

    def remove_job(self, name: str) -> None:
        job_id = self._job_ids.pop(name, None)
        if job_id is not None:
            self._inner.remove_job(job_id)
        self._originals.pop(name, None)
        self._schedule_write(lambda: self._store.unregister_job(name))

    def get_job(self, name: str) -> Callable[..., Any] | None:
        """Return the original callable, not the persistence wrapper."""
        return self._originals.get(name)

    async def restore_jobs(
        self,
        loader: Callable[[JobRecord], tuple[Callable[..., Any], Any]],
    ) -> None:
        """Re-register jobs from the database using a user-supplied loader."""
        self._loop_ref.bind()

        records = await self._store.list_jobs()

        for rec in records:
            # Make restore idempotent.
            if rec.name in self._originals:
                continue

            func, trigger = loader(rec)

            updated = JobRecord(
                name=rec.name,
                module=rec.module,
                qualname=rec.qualname,
                schedule_repr=repr(trigger),
                pass_context=rec.pass_context,
                created_at=rec.created_at or _now_utc(),
            )

            wrapped = _wrap_job(
                self._store,
                func,
                rec.name,
                pass_context=rec.pass_context,
                loop_ref=self._loop_ref,
                job_record=updated,
            )

            self._inner.register(
                wrapped,
                trigger=trigger,
                name=rec.name,
                pass_context=True,
            )

            self._originals[rec.name] = func

            def _register_write(record: JobRecord = updated) -> Any:
                return self._store.register_job(record)

            self._schedule_write(_register_write)

    # ---------------------------------------------------------------------
    # Transparent proxy
    # ---------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Guard private names to avoid infinite recursion during
        # copy/pickle/partial-init lookups.
        if name.startswith("_"):
            raise AttributeError(name)

        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Persistent Execution Loop
# ---------------------------------------------------------------------------


class PersistentExecutionLoop:
    """SQLite-persistent drop-in replacement for ``ExecutionLoop``.

    Parameters
    ----------
    db_path:
        Path to the SQLite database. Defaults to ``zeitwerkzeug.db`` in CWD.

        If ``registry`` is provided, this parameter is ignored and the
        registry's existing store is used.

    registry:
        Optional ``PersistentFuzzyCron`` instance. If supplied, its store
        is used by this loop.

    history_limit:
        Max in-memory history items.

    prune_older_than:
        Automatically delete execution records older than this on shutdown.
        Set to ``None`` to disable pruning.

    loop_kwargs:
        Forwarded to ``ExecutionLoop``.
    """

    def __init__(
        self,
        *,
        db_path: str | Path = "zeitwerkzeug.db",
        registry: PersistentFuzzyCron | None = None,
        history_limit: int = 1000,
        prune_older_than: timedelta | None = timedelta(days=30),
        **loop_kwargs: Any,
    ) -> None:
        self._prune_older_than = prune_older_than
        self._history_limit = history_limit
        self._initialized = False

        if registry is not None:
            self._store = registry._store
            self._registry = registry
        else:
            self._store = SQLiteStore(db_path)
            self._registry = PersistentFuzzyCron(
                self._store,
                registry=FuzzyCron(),
            )

        self._loop = ExecutionLoop(
            registry=cast(FuzzyCron, self._registry),
            history_limit=history_limit,
            **loop_kwargs,
        )

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    @property
    def registry(self) -> PersistentFuzzyCron:
        return self._registry

    @property
    def history(self) -> Sequence[Any]:
        return self._loop.history

    async def init(self) -> int:
        """Create tables, heal interrupted runs, restore history.

        Must be called once before ``run()``.

        Returns the number of crash-interrupted executions marked as failed.
        """
        if self._initialized:
            return 0

        await self._store.init()
        self._registry.bind_loop()

        # Flush job metadata registered before init().
        await self._registry.flush_pending_writes()

        # Crash recovery: rows stuck in 'running' belong to a dead process.
        healed = await self._store.heal_interrupted()

        # Restore recent history.
        # DB query is newest-first; in-memory history is oldest-first.
        # Restore recent history.
        # DB query is newest-first; in-memory history is oldest-first.
        restored = await self._store.get_history(limit=self._history_limit)

        internal_history = getattr(self._loop, "_history", None)
        if isinstance(internal_history, MutableSequence):
            internal_history.extend(reversed(restored))
        else:
            hist = self._loop.history
            if isinstance(hist, MutableSequence):
                hist.extend(reversed(restored))

        self._initialized = True
        return healed

    async def run(self, until: datetime | None = None) -> None:
        if not self._initialized:
            raise RuntimeError("PersistentExecutionLoop.init() must be called before run()")

        self._registry.bind_loop()

        # Make sure any queued job-metadata writes are durable before jobs run.
        await self._registry.flush_pending_writes()

        try:
            await self._loop.run(until=until)
        finally:
            await self._registry.wait_for_writes()

            if self._prune_older_than is not None and self._store.is_initialized:
                try:
                    deleted = await self._store.prune(self._prune_older_than)
                except Exception:
                    logger.exception("Pruning execution history failed")
                else:
                    if deleted:
                        try:
                            await self._store.vacuum()
                        except Exception:
                            logger.exception("VACUUM failed after pruning")

    async def stop(self) -> None:
        """Stop the inner execution loop.

        The underlying ``ExecutionLoop.stop()`` may be synchronous or
        asynchronous depending on the implementation/version, so support both.
        """
        stop_fn = cast(Callable[[], Any], self._loop.stop)
        result = stop_fn()

        if inspect.isawaitable(result):
            await result

    async def close(self) -> None:
        if self._store.is_initialized:
            await self._registry.flush_pending_writes()
            await self._registry.wait_for_writes()

        await self._store.close()

    # ---------------------------------------------------------------------
    # Diagnostics
    # ---------------------------------------------------------------------

    async def db_stats(self) -> dict[str, Any]:
        total = await self._store.execution_count()
        success = await self._store.execution_count(status="success")
        failed = await self._store.execution_count(status="failed")
        timeout = await self._store.execution_count(status="timeout")
        skipped = await self._store.execution_count(status="skipped")
        jobs = len(await self._store.list_jobs())

        return {
            "db_path": str(self._store.db_path),
            "total_executions": total,
            "successful": success,
            "failed": failed,
            "timeout": timeout,
            "skipped": skipped,
            "registered_jobs": jobs,
        }

    # ---------------------------------------------------------------------
    # Transparent proxy
    # ---------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        return getattr(self._loop, name)
