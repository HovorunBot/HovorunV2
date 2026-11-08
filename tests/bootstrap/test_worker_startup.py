"""Verify real worker resources close after partial startup and remain reusable on retry."""

import asyncio
import json
import sys

import pytest
from dishka import AsyncContainer
from sqlalchemy import Engine, event

from hovorunv2.bootstrap.worker_runtime import WorkerChildContext
from hovorunv2.domain.enums import WorkerRole
from hovorunv2.infrastructure.config import Settings
from tests.utils.runtime import runtime_cache


@pytest.mark.parametrize("failure", ["runtime", "telegram"])
async def test_failed_worker_startup_disposes_database_and_can_retry(
    test_container: AsyncContainer, postgres_database_url: str, failure: str
) -> None:
    """A later startup failure disposes acquired resources and leaves the same context retryable."""
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    original = settings.valkey_url if failure == "runtime" else settings.bot_token
    if failure == "runtime":
        settings.valkey_url = "invalid://startup-test"
    else:
        settings.bot_token = ""
    disposed: list[Engine] = []
    record_disposal = disposed.append
    event.listen(Engine, "engine_disposed", record_disposal)
    async with runtime_cache(test_container) as (_cache, namespace):
        context = WorkerChildContext(settings, namespace, role=WorkerRole.OPERATIONS)
        try:
            message = "Redis URL must specify" if failure == "runtime" else "Telegram transport requires"
            with pytest.raises(ValueError, match=message):
                context.open()
            assert disposed
            assert not context.opened
            context.close()
            if failure == "runtime":
                settings.valkey_url = original
            else:
                settings.bot_token = original
            disposed.clear()
            context.open()
            operations = context.operations
            assert operations.sweep_expired_cleanups() == 0
            context.close()
            assert disposed
            assert not context.opened
            with pytest.raises(ValueError, match="Worker operations runner is not initialized"):
                _ = context.operations
            context.close()
        finally:
            context.close()
            event.remove(Engine, "engine_disposed", record_disposal)


@pytest.mark.subprocess
@pytest.mark.browser
@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_missing_media_dependency_closes_acquired_resources(
    postgres_database_url: str, *, cleanup_failure: bool
) -> None:
    """A late dependency failure disposes the database and active browser, retaining caller-owned HTTP."""
    del postgres_database_url
    arguments = [sys.executable, "-W", "error", "-m", "tests.utils.worker_startup"]
    if cleanup_failure:
        arguments.append("--cleanup-failure")
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise
    assert process.returncode == 0, stderr.decode()
    assert not stderr, stderr.decode()
    observed = json.loads(stdout)
    assert observed["original_error"]
    assert observed["database_disposed"]
    assert observed["browser_stopped"]
    assert observed["browser_detached"]
    assert observed["caller_http_usable"]
