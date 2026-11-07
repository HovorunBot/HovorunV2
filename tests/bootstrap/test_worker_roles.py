"""Verify role-based worker runtime composition, lifecycle, and import isolation."""

import subprocess
import sys

import pytest

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_runtime import WorkerChildContext
from hovorunv2.domain.enums import WorkerRole
from hovorunv2.infrastructure.config import Settings
from tests.utils.runtime import ManualClock


@pytest.mark.unit
def test_worker_role_enum_values() -> None:
    """WorkerRole contains operations, media, browser, and all modes."""
    assert WorkerRole.OPERATIONS == "operations"
    assert WorkerRole.MEDIA == "media"
    assert WorkerRole.BROWSER == "browser"
    assert WorkerRole.ALL == "all"


async def test_operations_worker_role_composition(test_container: object, postgres_database_url: str) -> None:
    """Operations worker initializes WorkerOperationsRunner and denies FeatureJobRunner."""
    del test_container
    settings = Settings(
        database_url=postgres_database_url,
        bot_token="synthetic:token",  # noqa: S106
        _env_file=None,
    )
    clock = ManualClock()
    namespace = RuntimeNamespace("test:{ops}:")
    context = WorkerChildContext(
        settings=settings,
        namespace=namespace,
        safety=UsageSafetyCaps(),
        clock=clock,
        role=WorkerRole.OPERATIONS,
    )
    try:
        context.open()
        assert context.role == WorkerRole.OPERATIONS
        assert context.operations is not None

        with pytest.raises(ValueError, match="Feature job runner is not initialized"):
            _ = context.runner

        # Can run maintenance operations through the initialized operations runner
        assert context.operations.sweep_expired_cleanups() == 0
    finally:
        context.close()


async def test_media_worker_role_composition(test_container: object, postgres_database_url: str) -> None:
    """Media worker initializes FeatureJobRunner without browser and denies WorkerOperationsRunner."""
    del test_container
    settings = Settings(
        database_url=postgres_database_url,
        bot_token="synthetic:token",  # noqa: S106
        browser_enabled=True,  # Media role must not initialize browser even if settings say True
        _env_file=None,
    )
    clock = ManualClock()
    namespace = RuntimeNamespace("test:{media}:")
    context = WorkerChildContext(
        settings=settings,
        namespace=namespace,
        safety=UsageSafetyCaps(),
        clock=clock,
        role=WorkerRole.MEDIA,
    )
    try:
        context.open()
        assert context.role == WorkerRole.MEDIA
        assert context.runner is not None

        with pytest.raises(ValueError, match="Worker operations runner is not initialized"):
            _ = context.operations

        # Browser service must be None for pure media role
        assert context.browser is None
    finally:
        context.close()


async def test_browser_worker_role_composition(test_container: object, postgres_database_url: str) -> None:
    """Browser worker initializes FeatureJobRunner with browser support."""
    del test_container
    settings = Settings(
        database_url=postgres_database_url,
        bot_token="synthetic:token",  # noqa: S106
        browser_enabled=False,  # Browser role always attaches browser regardless of browser_enabled
        _env_file=None,
    )
    clock = ManualClock()
    namespace = RuntimeNamespace("test:{browser}:")
    context = WorkerChildContext(
        settings=settings,
        namespace=namespace,
        safety=UsageSafetyCaps(),
        clock=clock,
        role=WorkerRole.BROWSER,
    )
    try:
        context.open()
        assert context.role == WorkerRole.BROWSER
        assert context.runner is not None
        assert context.browser is not None
    finally:
        context.close()


@pytest.mark.unit
def test_operations_role_subprocess_import_isolation() -> None:
    """A fresh process initializing operations role does not load media/browser/bot packages."""
    code = """
import os, sys
os.environ['WORKER_ROLE'] = 'operations'
os.environ['BOT_TOKEN'] = 'synthetic:token'
import hovorunv2.bootstrap.worker_runtime as runtime
context = runtime.WorkerChildContext()
assert context.role == runtime.WorkerRole.OPERATIONS

forbidden = ['aiogram', 'dishka', 'yt_dlp', 'instaloader', 'bs4', 'DrissionPage']
loaded = [m for m in forbidden if m in sys.modules]
if loaded:
    print(f"FAILED: loaded {loaded}")
    sys.exit(1)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"


@pytest.mark.unit
def test_media_role_subprocess_import_isolation() -> None:
    """A fresh process initializing media role does not load DrissionPage, aiogram, or dishka."""
    code = """
import os, sys
os.environ['WORKER_ROLE'] = 'media'
os.environ['BOT_TOKEN'] = 'synthetic:token'
import hovorunv2.bootstrap.worker_runtime as runtime
context = runtime.WorkerChildContext()
assert context.role == runtime.WorkerRole.MEDIA

forbidden = ['aiogram', 'dishka', 'DrissionPage']
loaded = [m for m in forbidden if m in sys.modules]
if loaded:
    print(f"FAILED: loaded {loaded}")
    sys.exit(1)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
