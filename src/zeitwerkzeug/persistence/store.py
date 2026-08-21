"""SQLite-backed persistence store (aiosqlite).

Design notes
------------

aiosqlite runs a single dedicated background thread per connection and
serialises every operation onto it. That maps perfectly onto SQLite's
"one writer at a time" model, so this store:

- keeps exactly one connection (no thread-locals),
- uses ``isolation_level=None`` (autocommit — no manual ``commit()``),
- talks to it purely via ``await`` (no ``run_in_executor``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from .persistant_models import ExecutionRecord, JobRecord

logger = logging.getLogger(__name__)

UTC = UTC


def _iso_utc(value: datetime | None) -> str | None:
    """Serialize a datetime as an ISO-8601 UTC string."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS jobs (
    name          TEXT PRIMARY KEY,
    module        TEXT,
    qualname      TEXT,
    schedule_repr TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempt       INTEGER NOT NULL DEFAULT 1,
    triggered_at  TIMESTAMP,
    started_at    TIMESTAMP,
    finished_at   TIMESTAMP,
    error_message TEXT,
    context_data  TEXT DEFAULT '{}',
    FOREIGN KEY (job_name) REFERENCES jobs(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exec_job       ON executions(job_name);
CREATE INDEX IF NOT EXISTS idx_exec_status    ON executions(status);
CREATE INDEX IF NOT EXISTS idx_exec_triggered ON executions(triggered_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class SQLiteStore:
    """Async-friendly SQLite store backed by a single aiosqlite connection."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    # ---------------------------------------------------------------------
    # Connection management
    # ---------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        return self._db is not None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteStore is not initialized — await store.init() first")
        return self._db

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------

    async def init(self) -> None:
        """Open the connection, apply pragmas, run pending migrations."""
        if self._db is not None:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(
            str(self.db_path),
            isolation_level=None,
        )
        self._db.row_factory = aiosqlite.Row

        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        # Ensure meta exists before trying to read schema_version.
        await self._db.executescript(_BOOTSTRAP)

        raw_version = await self._meta_get("schema_version", "0")
        try:
            current = int(raw_version or 0)
        except ValueError:
            logger.warning(
                "Invalid schema_version %r in meta table; assuming 0",
                raw_version,
            )
            current = 0

        if current < 1:
            await self._db.executescript(_MIGRATION_1)
            await self._meta_set("schema_version", "1")
            current = 1

        # Migration 2:
        # Add pass_context if missing. This is done defensively so both
        # fresh databases and old version-1 databases converge correctly.
        await self._ensure_jobs_pass_context()

        if current < 2:
            await self._meta_set("schema_version", "2")
            current = 2

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _ensure_jobs_pass_context(self) -> None:
        """Add ``jobs.pass_context`` if it does not exist yet."""
        async with self._conn.execute("PRAGMA table_info(jobs)") as cur:
            columns = await cur.fetchall()

        if all(col["name"] != "pass_context" for col in columns):
            await self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN pass_context INTEGER NOT NULL DEFAULT 1"
            )

    # ---------------------------------------------------------------------
    # Row helpers
    # ---------------------------------------------------------------------

    async def _fetch_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> aiosqlite.Row | None:
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetch_all(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> Iterable[aiosqlite.Row]:
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    # ---------------------------------------------------------------------
    # Meta helpers
    # ---------------------------------------------------------------------

    async def _meta_set(self, key: str, value: str) -> None:
        await self._conn.execute(
            """
            INSERT INTO meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    async def _meta_get(
        self,
        key: str,
        default: str | None = None,
    ) -> str | None:
        row = await self._fetch_one(
            "SELECT value FROM meta WHERE key = ?",
            (key,),
        )
        return row["value"] if row else default

    # ---------------------------------------------------------------------
    # Job registry metadata
    # ---------------------------------------------------------------------

    async def register_job(self, record: JobRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO jobs (
                name,
                module,
                qualname,
                schedule_repr,
                pass_context,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                module=excluded.module,
                qualname=excluded.qualname,
                schedule_repr=excluded.schedule_repr,
                pass_context=excluded.pass_context
            """,
            record.to_db_row(),
        )

    async def unregister_job(self, name: str) -> None:
        await self._conn.execute(
            "DELETE FROM jobs WHERE name = ?",
            (name,),
        )

    async def list_jobs(self) -> list[JobRecord]:
        rows = await self._fetch_all("SELECT * FROM jobs ORDER BY created_at")
        return [JobRecord.from_db_row(r) for r in rows]

    # ---------------------------------------------------------------------
    # Execution history
    # ---------------------------------------------------------------------

    async def log_execution(self, record: ExecutionRecord) -> int:
        """Insert a record and return the generated row id."""
        async with self._conn.execute(
            """
            INSERT INTO executions (
                job_name,
                status,
                attempt,
                triggered_at,
                started_at,
                finished_at,
                error_message,
                context_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            record.to_db_row(),
        ) as cur:
            return cur.lastrowid  # type: ignore[return-value]

    async def update_execution(
        self,
        execution_id: int,
        *,
        status: str | None = None,
        finished_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        parts: list[str] = []
        params: list[Any] = []

        if status is not None:
            parts.append("status = ?")
            params.append(status)

        if finished_at is not None:
            parts.append("finished_at = ?")
            params.append(_iso_utc(finished_at))

        if error_message is not None:
            parts.append("error_message = ?")
            params.append(error_message)

        if not parts:
            return

        params.append(execution_id)

        await self._conn.execute(
            f"UPDATE executions SET {', '.join(parts)} WHERE id = ?",
            tuple(params),
        )

    async def get_history(
        self,
        *,
        job_name: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[ExecutionRecord]:
        sql = "SELECT * FROM executions WHERE 1=1"
        params: list[Any] = []

        if job_name:
            sql += " AND job_name = ?"
            params.append(job_name)

        if status:
            sql += " AND status = ?"
            params.append(status)

        if since:
            sql += " AND triggered_at >= ?"
            params.append(_iso_utc(since))

        if until:
            sql += " AND triggered_at <= ?"
            params.append(_iso_utc(until))

        sql += " ORDER BY triggered_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self._fetch_all(sql, tuple(params))
        return [ExecutionRecord.from_db_row(r) for r in rows]

    async def get_last_execution(
        self,
        job_name: str,
    ) -> ExecutionRecord | None:
        rows = await self.get_history(job_name=job_name, limit=1)
        return rows[0] if rows else None

    async def execution_count(
        self,
        *,
        job_name: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM executions WHERE 1=1"
        params: list[Any] = []

        if job_name:
            sql += " AND job_name = ?"
            params.append(job_name)

        if status:
            sql += " AND status = ?"
            params.append(status)

        if since:
            sql += " AND triggered_at >= ?"
            params.append(_iso_utc(since))

        row = await self._fetch_one(sql, tuple(params))
        return row["n"] if row else 0

    # ---------------------------------------------------------------------
    # Maintenance
    # ---------------------------------------------------------------------

    async def prune(self, older_than: timedelta) -> int:
        """Delete records older than the given delta.

        Returns rows deleted.
        """
        cutoff = _iso_utc(datetime.now(UTC) - older_than)

        cur = await self._conn.execute(
            """
            DELETE FROM executions
            WHERE COALESCE(triggered_at, started_at, finished_at) < ?
            """,
            (cutoff,),
        )

        return int(cur.rowcount or 0)

    async def vacuum(self) -> None:
        """Reclaim disk space after heavy pruning."""
        await self._conn.execute("VACUUM")

    async def heal_interrupted(self) -> int:
        """Mark in-flight ``running`` rows from a crashed process as failed.

        The wrappers in ``persistance_loop.py`` write ``status='running'``
        when a job starts, so any row still in that state at startup belongs
        to a process that died mid-execution.

        Returns number of rows healed.
        """
        cur = await self._conn.execute(
            """
            UPDATE executions
            SET status = 'failed',
                finished_at = ?,
                error_message = 'Daemon restarted while job was in flight'
            WHERE status = 'running'
            """,
            (_iso_utc(datetime.now(UTC)),),
        )

        return int(cur.rowcount or 0)
