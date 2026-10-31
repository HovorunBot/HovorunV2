"""Real Celery prefork worker lifecycle for native recovery transport acceptance."""

import asyncio
import logging
import multiprocessing
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from os import getpid

import redis
from celery import Celery
from celery.signals import worker_process_init

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_runtime import init_worker_child, on_worker_process_init
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.queues.feature_queue import register_feature_tasks
from hovorunv2.infrastructure.runtime.cache import CacheService


def _initialize_probe_child(settings: Settings, namespace: RuntimeNamespace, **kwargs: object) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        msg = "Recovery worker must use native synchronous execution."
        raise AssertionError(msg)
    init_worker_child(settings, namespace, UsageSafetyCaps())
    with redis.Redis.from_url(settings.valkey_url) as client:
        client.set(namespace.value + "probe:ready", getpid(), ex=60)


def _run_worker(settings: Settings, namespace: RuntimeNamespace) -> None:
    logging.disable(logging.CRITICAL)
    worker_process_init.disconnect(on_worker_process_init)
    worker_process_init.connect(partial(_initialize_probe_child, settings, namespace), weak=False)
    app = Celery("recovery_probe", broker=settings.valkey_url)
    app.conf.update(
        broker_transport_options={"global_keyprefix": namespace.value},
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        worker_prefetch_multiplier=1,
    )
    register_feature_tasks(app)
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
                "--queues=" + namespace.value + "operations",
            ]
        )
    finally:
        app.close()


@asynccontextmanager
async def native_recovery_worker(
    settings: Settings,
    namespace: RuntimeNamespace,
    cache: CacheService,
) -> AsyncIterator[None]:
    """Start a real worker parent and prefork child; always terminate and join both on exit."""
    process = multiprocessing.get_context("spawn").Process(target=_run_worker, args=(settings, namespace))
    process.start()
    try:
        async with asyncio.timeout(20):
            while not await cache.client.exists(namespace.value + "probe:ready"):
                assert process.is_alive()
                await asyncio.sleep(0.05)
        child_pid = await cache.client.get(namespace.value + "probe:ready")
        assert int(child_pid) != process.pid
        yield
    finally:
        process.terminate()
        await asyncio.to_thread(process.join, 20)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
        process.close()
