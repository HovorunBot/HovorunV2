"""Generic background task queue infrastructure based on SAQ."""

import asyncio
import contextlib
from collections.abc import Callable
from functools import partial
from typing import Any

from saq import CronJob, Worker
from saq.queue.redis import RedisQueue

from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class TaskQueueService:
    """Generic async task queue and worker manager.

    Provides a clean infrastructure abstraction over SAQ:
    - Enqueue asynchronous background jobs.
    - Register recurring cron jobs.
    - Manage background worker lifecycle independent of business domain.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize task queue with connection settings."""
        self._settings = settings
        self._queue: RedisQueue | None = None
        self._worker: Worker | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._cron_jobs: list[CronJob[Any]] = []
        self._registered_functions: list[Callable[..., Any]] = []

    def get_queue(self) -> RedisQueue:
        """Get or initialize SAQ RedisQueue."""
        if self._queue is None:
            self._queue = RedisQueue.from_url(self._settings.valkey_url, name="hovorun_tasks")
        return self._queue

    async def _worker_startup(self, ctx: dict[str, Any], *, worker_context: dict[str, Any]) -> None:
        """Install the supplied context when the worker starts."""
        ctx.update(worker_context)

    def register_function(self, fn: Callable[..., Any]) -> None:
        """Register a callable function for worker execution."""
        if fn not in self._registered_functions:
            self._registered_functions.append(fn)

    def register_cron(self, fn: Callable[..., Any], cron: str) -> None:
        """Register a recurring cron job."""
        self.register_function(fn)
        self._cron_jobs.append(CronJob(fn, cron=cron))

    async def enqueue(self, function_name: str, **kwargs: Any) -> Any:
        """Enqueue a background task by name."""
        queue = self.get_queue()
        return await queue.enqueue(function_name, **kwargs)

    async def start_worker(
        self,
        context: dict[str, Any] | None = None,
        concurrency: int = 2,
    ) -> None:
        """Start the background SAQ worker loop."""
        if self._worker_task and not self._worker_task.done():
            return

        queue = self.get_queue()
        worker_context = context or {}

        self._worker = Worker(
            queue=queue,
            functions=self._registered_functions,
            cron_jobs=self._cron_jobs,
            startup=partial(self._worker_startup, worker_context=worker_context),
            concurrency=concurrency,
        )
        self._worker_task = asyncio.create_task(self._worker.start())
        logger.info(
            "TaskQueueService worker started with %d functions and %d cron jobs",
            len(self._registered_functions),
            len(self._cron_jobs),
        )

    async def stop_worker(self) -> None:
        """Stop background worker and disconnect queue."""
        if self._worker:
            await self._worker.stop()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        if self._queue:
            await self._queue.disconnect()
            self._queue = None
        logger.info("TaskQueueService worker stopped")
