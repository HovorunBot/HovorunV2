"""Native worker queue/beat contracts and lean-runtime import isolation."""

import asyncio
import sys

import pytest

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.bootstrap.worker_app import build_worker_application
from hovorunv2.infrastructure.config import BrokerSettings
from hovorunv2.infrastructure.queues.feature_queue import (
    CHAT_MAINTENANCE_TASK,
    CLEANUP_SWEEP_TASK,
    MEMBERSHIP_RECONCILIATION_TASK,
    NOTIFICATION_DELIVERY_TASK,
    RECOVERY_TASK_FUNCTION,
    RELEASE_NOTIFICATIONS_TASK,
)


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
def test_worker_beat_schedule_routes_all_sweeps_to_operations() -> None:
    """The singleton Beat schedules every maintenance and recovery sweep onto operations with the expected cadence."""
    namespace = RuntimeNamespace()
    app = build_worker_application(_settings(), namespace)
    try:
        schedule = app.conf.beat_schedule
        assert set(schedule) == {
            "membership_reconciliation",
            "chat_maintenance",
            "cleanup_sweep",
            "release_notifications",
            "notification_delivery",
            "recovery_sweeps",
        }
        assert {entry["task"] for entry in schedule.values()} == {
            MEMBERSHIP_RECONCILIATION_TASK,
            CHAT_MAINTENANCE_TASK,
            CLEANUP_SWEEP_TASK,
            RELEASE_NOTIFICATIONS_TASK,
            NOTIFICATION_DELIVERY_TASK,
            RECOVERY_TASK_FUNCTION,
        }
        assert {entry["options"]["queue"] for entry in schedule.values()} == {namespace.value + "operations"}
        assert schedule["chat_maintenance"]["schedule"].minute == {0}
        assert schedule["chat_maintenance"]["schedule"].hour == set(range(24))
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
