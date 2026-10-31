"""Native worker and deployment-role contracts for independently started containers."""

import asyncio
import re
import sys
from pathlib import Path

import pytest

from hovorunv2.application.budgets import RuntimeNamespace
from hovorunv2.bootstrap.worker_app import build_worker_application
from hovorunv2.infrastructure.config import BrokerSettings
from hovorunv2.infrastructure.feature_queue import build_beat_schedule

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROLES = ("bot", "worker-media", "worker-browser", "worker-operations", "beat", "migrate")


def _settings() -> BrokerSettings:
    return BrokerSettings(valkey_url="redis://localhost:6379/9", _env_file=None)


def _dockerfile_stage(dockerfile: str, name: str) -> str:
    marker = f" AS {name}\n"
    start = dockerfile.index(marker) + len(marker)
    following = re.search(r"^FROM ", dockerfile[start:], flags=re.MULTILINE)
    return dockerfile[start:] if following is None else dockerfile[start : start + following.start()]


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


@pytest.mark.unit
def test_compose_files_wire_independent_application_roles() -> None:
    """Both standalone stacks expose bot, native workers, Beat and migration as separate services."""
    namespace = RuntimeNamespace()
    for filename in ("docker-compose.dev.yml", "docker-compose.prod.yml"):
        compose = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
        for role in APPLICATION_ROLES:
            assert re.search(rf"^  {role}:$", compose, flags=re.MULTILINE), f"{filename}: {role}"
        for role in ("media", "browser", "operations"):
            assert f"--queues={namespace.value}{role}" in compose, f"{filename}: {role} queue"
        assert "entrypoint:" not in compose


@pytest.mark.unit
def test_image_targets_keep_browser_and_legacy_import_dependencies_out_of_lean_runtime() -> None:
    """Lean roles contain no Chromium or SQLite importer dependency; browser builds them at build time."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "entrypoint" not in dockerfile.lower()
    assert "ENTRYPOINT [" not in dockerfile
    lean = _dockerfile_stage(dockerfile, "lean")
    browser = _dockerfile_stage(dockerfile, "browser")
    legacy_import = _dockerfile_stage(dockerfile, "legacy-import")
    assert "chromium" not in lean
    assert "--extra browser" not in lean
    assert "--extra legacy-import" not in lean
    assert "chromium" in browser
    assert "--extra browser" in browser
    assert "--extra legacy-import" in legacy_import


@pytest.mark.unit
def test_environment_template_and_repository_remove_obsolete_startup_secrets() -> None:
    """Entrypoint/sentinel artifacts and the unsupported OpenRouter template key stay absent."""
    assert "entrypoint" not in {path.name for path in PROJECT_ROOT.iterdir()}
    example = (PROJECT_ROOT / "example.env").read_text(encoding="utf-8")
    assert "OPENROUTER" not in example
    production = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "hovorun:hovorun" not in production


@pytest.mark.subprocess
async def test_lean_worker_import_does_not_load_browser_packages() -> None:
    """A media/operations/Beat process starts without importing DrissionPage or the browser adapter."""
    script = (
        "import sys\n"
        "import hovorunv2.bootstrap.worker_app\n"
        "assert 'DrissionPage' not in sys.modules\n"
        "assert 'hovorunv2.infrastructure.browser' not in sys.modules\n"
        "assert 'hovorunv2.infrastructure.native_browser' not in sys.modules\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-W", "error", "-c", script)
    assert await process.wait() == 0
