"""SQLite persistence layer for zeitwerkzeug.

Quick start
-----------

.. code-block:: python

    from zeitwerkzeug.persistence import PersistentExecutionLoop
    from datetime import timedelta

    loop = PersistentExecutionLoop(
        db_path="scheduler.db",
        max_concurrency=4,
        default_job_timeout=timedelta(minutes=2),
    )

    await loop.init()
    loop.registry.add_job(my_task, trigger=my_trigger, name="my_task")
    await loop.run()
"""

from .persistance_loop import PersistentExecutionLoop, PersistentFuzzyCron
from .persistant_models import ExecutionRecord, JobRecord
from .store import SQLiteStore

__all__ = [
    "ExecutionRecord",
    "JobRecord",
    "PersistentExecutionLoop",
    "PersistentFuzzyCron",
    "SQLiteStore",
]
