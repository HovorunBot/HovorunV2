"""Real Celery worker parent and prefork child for restart and queue-isolation tests."""

import asyncio
import logging
import multiprocessing
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from uuid import uuid4

import redis
from celery.signals import worker_process_init, worker_ready

from hovorunv2.application.budgets import RuntimeNamespace
from hovorunv2.bootstrap.worker_app import build_worker_application
from hovorunv2.bootstrap.worker_runtime import init_worker_child, on_worker_process_init
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import BrokerSettings, Settings

READY_TTL_SECONDS = 60
READY_WAIT_SECONDS = 20
STOP_SECONDS = 20


def _record_process(key: str, broker_url: str, **kwargs: object) -> None:
    """Publish a worker parent or child PID to the isolated test namespace."""
    client = redis.Redis.from_url(broker_url, decode_responses=True)
    try:
        client.set(key, os.getpid(), ex=READY_TTL_SECONDS)
    finally:
        client.close()


def _initialize_child(settings: Settings, namespace: RuntimeNamespace, **kwargs: object) -> None:
    """Open child resources with the test namespace while accepting Celery signal kwargs."""
    init_worker_child(settings, namespace)


def _run_worker(
    broker_url: str,
    namespace: RuntimeNamespace,
    queues: tuple[str, ...],
    ready_key: str,
    child_key: str,
) -> None:
    logging.disable(logging.CRITICAL)
    app = build_worker_application(BrokerSettings(valkey_url=broker_url, _env_file=None), namespace)
    settings = Settings()
    worker_ready.connect(partial(_record_process, ready_key, broker_url), weak=False)
    worker_process_init.disconnect(on_worker_process_init)
    worker_process_init.connect(partial(_initialize_child, settings, namespace), weak=False)
    worker_process_init.connect(partial(_record_process, child_key, broker_url), weak=False)
    try:
        app.worker_main(
            [
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "--loglevel=ERROR",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "--queues=" + ",".join(queues),
            ]
        )
    finally:
        app.close()


async def _wait_ready(
    cache: CacheService,
    process: multiprocessing.Process,
    ready_key: str,
    child_key: str,
) -> None:
    async with asyncio.timeout(READY_WAIT_SECONDS):
        while not (await cache.client.exists(ready_key) and await cache.client.exists(child_key)):
            assert process.is_alive()
            await asyncio.sleep(0.05)


async def _stop_worker(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
    await asyncio.to_thread(process.join, STOP_SECONDS)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, 5)
    process.close()


@dataclass
class NativeWorkerHandle:
    """Control one real worker parent and its current prefork child."""

    process: multiprocessing.Process
    child_key: str
    cache: CacheService

    async def child_pid(self) -> int:
        """Read the current child PID without assuming a replacement exists."""
        raw = await self.cache.client.get(self.child_key)
        if raw is None:
            msg = "Native worker child has not started."
            raise ValueError(msg)
        return int(raw)

    async def wait_for_new_child(self, previous_pid: int) -> int:
        """Wait until Celery replaces a killed child, keeping the same broker namespace."""
        async with asyncio.timeout(READY_WAIT_SECONDS):
            while True:
                raw = await self.cache.client.get(self.child_key)
                if raw is not None and int(raw) != previous_pid:
                    return int(raw)
                assert self.process.is_alive()
                await asyncio.sleep(0.05)

    async def kill_child(self) -> None:
        """SIGKILL the executing child so the prefork parent must replace it."""
        os.kill(await self.child_pid(), signal.SIGKILL)


@asynccontextmanager
async def native_feature_worker(
    settings: Settings,
    namespace: RuntimeNamespace,
    queues: tuple[str, ...],
) -> AsyncIterator[NativeWorkerHandle]:
    """Start a real synchronous Celery worker and always terminate its process tree."""
    ready_key = namespace.value + "probe:ready:" + uuid4().hex
    child_key = namespace.value + "probe:child:" + uuid4().hex
    process = multiprocessing.get_context("spawn").Process(
        target=_run_worker,
        args=(settings.valkey_url, namespace, queues, ready_key, child_key),
    )
    process.start()
    cache = CacheService(settings.valkey_url)
    try:
        await _wait_ready(cache, process, ready_key, child_key)
        yield NativeWorkerHandle(process, child_key, cache)
    finally:
        await _stop_worker(process)
        await cache.close()
