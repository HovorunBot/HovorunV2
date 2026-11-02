"""Native worker queue/beat contracts and lean-runtime import isolation."""

import asyncio
import sys

import pytest

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.bootstrap.worker_app import build_worker_application
from hovorunv2.infrastructure.config import BrokerSettings
from hovorunv2.infrastructure.queues.feature_queue import build_beat_schedule


def _settings() -> BrokerSettings:
    return BrokerSettings(valkey_url="redis://localhost:6379/9", _env_file=None)


@pytest.mark.unit
def test_worker_application_uses_namespaced_role_queues() -> None:
    """Workers consume the exact namespaced queues used by bot publication and Beat."""
    namespace = RuntimeNamespace()
    app = build_worker_application(_settings(), namespace)
    try:
        assert {queue.name for queue in app.conf.task_queues} == {
            namespace.value + "media",
            namespace.value + "browser",
            namespace.value + "operations",
        }
        assert app.conf.task_default_queue == namespace.value + "media"
        assert app.conf.broker_transport_options["global_keyprefix"] == namespace.value
    finally:
        app.close()


@pytest.mark.unit
def test_worker_beat_schedule_targets_namespaced_operations_queue() -> None:
    """The singleton Beat publishes every maintenance and recovery sweep to operations."""
    namespace = RuntimeNamespace()
    app = build_worker_application(_settings(), namespace)
    try:
        assert app.conf.beat_schedule == build_beat_schedule(namespace.value)
        assert {entry["options"]["queue"] for entry in app.conf.beat_schedule.values()} == {
            namespace.value + "operations"
        }
    finally:
        app.close()


@pytest.mark.subprocess
async def test_lean_worker_import_does_not_load_browser_packages() -> None:
    """A media/operations/Beat process starts without importing DrissionPage or the browser adapter."""
    script = (
        "import sys\n"
        "import hovorunv2.bootstrap.worker_app\n"
        "assert 'DrissionPage' not in sys.modules\n"
        "assert 'hovorunv2.infrastructure.browser.async_browser' not in sys.modules\n"
        "assert 'hovorunv2.infrastructure.browser.native_browser' not in sys.modules\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-W", "error", "-c", script)
    assert await process.wait() == 0
