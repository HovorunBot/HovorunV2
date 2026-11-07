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


@pytest.mark.unit
def test_dockerfile_multi_stage_role_contracts() -> None:
    """Dockerfile defines dedicated role targets with BuildKit caching and minimal runtime layers."""
    dockerfile_path = Path(__file__).resolve().parents[2] / "Dockerfile"
    content = dockerfile_path.read_text(encoding="utf-8")

    expected_stages = [
        "FROM ${PYTHON_IMAGE} AS base",
        "FROM base AS builder-base",
        "FROM builder-base AS builder-migrate",
        "FROM builder-base AS builder-bot",
        "FROM builder-base AS builder-worker-ops",
        "FROM builder-base AS builder-worker-media",
        "FROM builder-base AS builder-worker-browser",
        "FROM base AS migrate",
        "FROM base AS bot",
        "FROM base AS worker-operations",
        "FROM base AS worker-media",
        "FROM base AS worker-browser",
        "FROM bot AS default",
    ]
    for stage in expected_stages:
        assert stage in content, f"Missing expected stage: {stage}"

    assert "--mount=type=cache,target=/root/.cache/uv" in content
    assert "--mount=type=cache,target=/var/cache/apt" in content

    runtime_targets = ["migrate", "bot", "worker-operations", "worker-media", "worker-browser"]
    sections = content.split("FROM base AS ")
    for section in sections[1:]:
        stage_name = section.split()[0]
        if stage_name in runtime_targets:
            stage_body = section.split("FROM ")[0]
            assert "/bin/uv" not in stage_body, f"Stage {stage_name} must not copy or invoke /bin/uv"
            assert "COPY src" not in stage_body, f"Stage {stage_name} must not duplicate src/ tree"
            assert "USER appuser" in stage_body, f"Stage {stage_name} must run as non-root appuser"
            assert "/app/.venv/bin/" in stage_body, f"Stage {stage_name} must execute /app/.venv/bin/* entrypoint"


@pytest.mark.unit
@pytest.mark.parametrize("compose_file", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
def test_compose_role_targets_and_direct_entrypoints(compose_file: str) -> None:
    """Compose manifests configure dedicated role targets and direct .venv commands without uv."""
    compose_path = Path(__file__).resolve().parents[2] / compose_file
    content = compose_path.read_text(encoding="utf-8")
    assert "uv run" not in content, f"{compose_file} must not invoke 'uv run'"

    data = yaml.safe_load(content)
    services = data["services"]

    expected_targets = {
        "migrate": "migrate",
        "bot": "bot",
        "worker-media": "worker-media",
        "worker-browser": "worker-browser",
        "worker-operations": "worker-operations",
        "beat": "worker-operations",
    }
    for service_name, target in expected_targets.items():
        service = services[service_name]
        assert service["build"]["target"] == target, f"{service_name} build target must be {target}"

    expected_roles = {
        "worker-media": "media",
        "worker-browser": "browser",
        "worker-operations": "operations",
        "beat": "operations",
    }
    for service_name, role in expected_roles.items():
        env = services[service_name]["environment"]
        assert env.get("WORKER_ROLE") == role, f"{service_name} must have WORKER_ROLE={role}"

    for service_name in ["migrate", "bot", "worker-media", "worker-browser", "worker-operations", "beat"]:
        command = services[service_name].get("command")
        assert command, f"{service_name} must specify a command"
        cmd_str = " ".join(command) if isinstance(command, list) else str(command)
        assert "/app/.venv/bin/" in cmd_str, f"{service_name} command must execute from /app/.venv/bin/: {cmd_str}"


@pytest.mark.unit
def test_docker_stack_build_roles_match_compose_build_services() -> None:
    """Every role configured for building in docker-stack.py matches a distinct Compose build target."""
    script_path = Path(__file__).resolve().parents[2] / "scripts/docker-stack.py"
    content = script_path.read_text(encoding="utf-8")
    expected_roles = {"migrate", "bot", "worker-operations", "worker-media", "worker-browser"}
    for role in expected_roles:
        assert f'"{role}"' in content or f"'{role}'" in content

    for compose_file in ["docker-compose.prod.yml", "docker-compose.dev.yml"]:
        compose_path = Path(__file__).resolve().parents[2] / compose_file
        services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
        build_targets = {svc["build"]["target"] for svc in services.values() if "build" in svc}
        assert build_targets == expected_roles
