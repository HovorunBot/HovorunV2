"""Native worker queue/beat contracts and lean-runtime import isolation."""

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

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
            namespace.value + "compression",
            namespace.value + "speech",
            namespace.value + "operations",
        }
        assert "run_compression_job" in app.tasks
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
    """A media/operations/Beat process starts without importing either browser SDK or the browser adapter."""
    script = (
        "import sys\n"
        "import hovorunv2.bootstrap.worker_app\n"
        "assert 'pydoll' not in sys.modules\n"
        "assert 'hovorunv2.infrastructure.browser.async_browser' not in sys.modules\n"
        "assert 'hovorunv2.infrastructure.browser.native_browser' not in sys.modules\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-W", "error", "-c", script)
    assert await process.wait() == 0


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
def test_translation_model_volume_is_persistent_and_role_scoped(filename: str) -> None:
    """All caption inference roles share the model cache, while lean roles have no model mount."""
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / filename).read_text())
    assert "translation_models" in compose["volumes"]
    for role in ("worker-media", "worker-browser", "worker-compression"):
        assert "translation_models:/app/data/models" in compose["services"][role].get("volumes", [])
        assert compose["services"][role]["environment"]["TRANSLATION_MODEL_DIR"].startswith("/app/data/models/")
    for role in ("bot", "migrate", "worker-operations", "beat"):
        assert not any("translation_models" in mount for mount in compose["services"][role].get("volumes", []))


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
def test_speech_worker_is_optional_bounded_and_uses_readonly_assets(filename: str) -> None:
    """Speech enablement adds one CPU worker without exposing private inputs to persistent storage."""
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / filename).read_text())
    speech = compose["services"]["worker-speech"]
    assert speech["profiles"] == ["speech"]
    assert speech["read_only"] is True
    assert speech["init"] is True
    assert speech["cpus"] == 2
    assert speech["mem_limit"] == "2g"
    assert speech["ulimits"]["core"] == 0
    assert speech["volumes"] == ["speech_models:/app/data/models/speech:ro"]
    assert speech["container_name"]
    assert "--concurrency=1" in speech["command"]
    assert "--prefetch-multiplier=1" in speech["command"]
    assert "--queues=work:{work}:speech" in speech["command"]
    assert speech["environment"]["WORKER_ROLE"] == "speech"
    assert "speech_models" in compose["volumes"]
    assert compose["services"]["bot"]["environment"]["SPEECH_ENABLED"] == "${SPEECH_ENABLED:-False}"


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
def test_compression_serializes_model_and_video_memory_within_its_container(filename: str) -> None:
    """Prevent concurrent model allocations from exceeding the default compression memory allowance."""
    root = Path(__file__).resolve().parents[2]
    service = yaml.safe_load((root / filename).read_text())["services"]["worker-compression"]
    assert "--concurrency=1" in service["command"]
    assert service["mem_limit"] == "${WORKER_COMPRESSION_MEMORY_LIMIT:-2g}"
    assert service["cpus"] == 2
