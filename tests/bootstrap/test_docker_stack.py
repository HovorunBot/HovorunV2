"""Exercise build selection, failure cleanup and scoped artifact removal through the operator CLI."""

import asyncio
import fcntl
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.unit, pytest.mark.subprocess]


@dataclass
class StackScenario:
    """Run the actual operator script in a disposable project with a recording Docker executable."""

    root: Path
    environment: dict[str, str]

    async def run(self, *command: str) -> int:
        """Run one stack command and return its external process exit status."""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self.root / "scripts/docker-stack.py"),
            "--environment",
            "dev",
            "--env-file",
            str(self.root / ".env"),
            "--version",
            "test",
            "--",
            *command,
            env=self.environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await process.communicate()
        assert b"synthetic-private-credential" not in output + error
        assert process.returncode is not None
        return process.returncode

    def calls(self) -> list[list[str]]:
        """Read recorded external calls without exposing resolved configuration."""
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]


def _scenario(root: Path, browser_enabled: str) -> StackScenario:
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copyfile(PROJECT_ROOT / "scripts/docker-stack.py", scripts / "docker-stack.py")
    executable = root / "docker"
    source = (PROJECT_ROOT / "tests/utils/docker_cli.py").read_text()
    executable.write_text(f"#!{sys.executable}\n{source}")
    executable.chmod(0o700)
    configuration = {
        "name": f"test-{uuid4().hex}",
        "services": {
            "bot": {"image": "hovorunv2-dev:test", "build": {"target": "lean"}},
            "worker-browser": {"image": "hovorunv2-dev-browser:test", "build": {"target": "browser"}},
            "worker-media": {
                "environment": {
                    "BROWSER_ENABLED": browser_enabled,
                    "BOT_TOKEN": "synthetic-private-credential",
                }
            },
        },
    }
    return StackScenario(
        root,
        os.environ
        | {
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "DOCKER_TEST_CONFIG": json.dumps(configuration),
            "DOCKER_TEST_LOG": str(root / "calls.jsonl"),
        },
    )


@pytest.mark.parametrize("enabled", ["False", "True"])
async def test_build_selects_browser_and_always_removes_its_builder(tmp_path: Path, enabled: str) -> None:
    """Build only selected targets without cache reuse and discard temporary builder state."""
    scenario = _scenario(tmp_path, enabled)
    assert await scenario.run("build") == 0
    calls = scenario.calls()
    builds = [call for call in calls if call[:2] == ["buildx", "build"]]
    assert [call[call.index("--target") + 1] for call in builds] == (
        ["lean", "browser"] if enabled == "True" else ["lean"]
    )
    assert all("--no-cache" in call and "--load" in call for call in builds)
    creation = next(call for call in calls if call[:2] == ["buildx", "create"])
    builder = creation[creation.index("--name") + 1]
    assert calls[-1] == ["buildx", "rm", "--force", builder]
    assert "--keep-state" not in calls[-1]


async def test_failed_build_removes_builder_and_preserves_failure_status(tmp_path: Path) -> None:
    """Failed image builds cannot leave temporary build caches behind."""
    scenario = _scenario(tmp_path, "False")
    scenario.environment["DOCKER_TEST_FAIL"] = "1"
    assert await scenario.run("build") == 7
    assert scenario.calls()[-1][:3] == ["buildx", "rm", "--force"]


async def test_build_skips_when_all_images_already_exist(tmp_path: Path) -> None:
    """Skip creating builders and building images when all configured targets exist."""
    scenario = _scenario(tmp_path, "True")
    scenario.environment["DOCKER_TEST_EXISTING_IMAGES"] = "hovorunv2-dev:test\nhovorunv2-dev-browser:test\n"
    assert await scenario.run("build") == 0
    calls = scenario.calls()
    inspects = [call for call in calls if call[:2] == ["image", "inspect"]]
    assert len(inspects) == 2
    assert not any(call[:1] == ["buildx"] for call in calls)


async def test_build_builds_only_missing_image_when_one_exists(tmp_path: Path) -> None:
    """Build only the absent image target when another role is already present."""
    scenario = _scenario(tmp_path, "True")
    scenario.environment["DOCKER_TEST_EXISTING_IMAGES"] = "hovorunv2-dev:test\n"
    assert await scenario.run("build") == 0
    calls = scenario.calls()
    builds = [call for call in calls if call[:2] == ["buildx", "build"]]
    assert len(builds) == 1
    assert builds[0][builds[0].index("--target") + 1] == "browser"


async def test_build_force_rebuilds_all_existing_images(tmp_path: Path) -> None:
    """Force flag rebuilds all role targets even when images already exist locally."""
    scenario = _scenario(tmp_path, "True")
    scenario.environment["DOCKER_TEST_EXISTING_IMAGES"] = "hovorunv2-dev:test\nhovorunv2-dev-browser:test\n"
    assert await scenario.run("build", "--force") == 0
    calls = scenario.calls()
    builds = [call for call in calls if call[:2] == ["buildx", "build"]]
    assert [call[call.index("--target") + 1] for call in builds] == ["lean", "browser"]


async def test_build_force_via_environment_rebuilds_all_existing_images(tmp_path: Path) -> None:
    """Environment FORCE variable rebuilds role targets even when images already exist."""
    scenario = _scenario(tmp_path, "True")
    scenario.environment["FORCE"] = "1"
    scenario.environment["DOCKER_TEST_EXISTING_IMAGES"] = "hovorunv2-dev:test\nhovorunv2-dev-browser:test\n"
    assert await scenario.run("build") == 0
    calls = scenario.calls()
    builds = [call for call in calls if call[:2] == ["buildx", "build"]]
    assert [call[call.index("--target") + 1] for call in builds] == ["lean", "browser"]


@pytest.mark.parametrize("enabled", ["False", "True"])
async def test_startup_selects_profile_and_removes_disabled_browser(tmp_path: Path, enabled: str) -> None:
    """A deployment toggle controls both the new service set and any previously running browser worker."""
    scenario = _scenario(tmp_path, enabled)
    assert await scenario.run("up", "-d", "--no-build") == 0
    calls = scenario.calls()
    startup = next(call for call in calls if "up" in call)
    assert ("browser" in startup) == (enabled == "True")
    assert startup[-3:] == ["--wait", "--wait-timeout", "180"]
    stops = [call for call in calls if "--stop" in call]
    assert bool(stops) == (enabled == "False")
    if stops:
        assert stops[0][-1] == "worker-browser"
    assert not any(call[-2:] == ["rm", "--force"] for call in calls)
    assert not any("--volumes" in call or "-v" in call for call in calls)


async def test_cleanup_preserves_live_images_foreign_images_and_application_data(tmp_path: Path) -> None:
    """Remove unused application images and build artifacts without touching state or other projects."""
    scenario = _scenario(tmp_path, "False")
    images = [
        {"ID": "sha256:old", "Repository": "hovorunv2-dev", "Tag": "old"},
        {"ID": "sha256:old", "Repository": "hovorunv2-dev", "Tag": "older"},
        {"ID": "sha256:running", "Repository": "hovorunv2-dev", "Tag": "current"},
        {"ID": "sha256:foreign", "Repository": "other-app", "Tag": "latest"},
        {"ID": "sha256:shared", "Repository": "hovorunv2-dev", "Tag": "shared"},
        {"ID": "sha256:shared", "Repository": "other-app", "Tag": "shared"},
    ]
    scenario.environment["DOCKER_TEST_IMAGES"] = "\n".join(json.dumps(item) for item in images)
    scenario.environment["DOCKER_TEST_LABELLED"] = "sha256:dangling\nsha256:shared\n"
    for name in ("build", "dist", ".venv", "data", "src/__pycache__"):
        (tmp_path / name).mkdir(parents=True)
        (tmp_path / name / "keep-or-remove").touch()
    (tmp_path / ".env").write_text("synthetic-private-credential")
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = True\n")
    assert await scenario.run("clean") == 0
    removals = [call for call in scenario.calls() if call[:2] == ["image", "rm"]]
    assert removals == [
        ["image", "rm", "sha256:dangling"],
        ["image", "rm", "hovorunv2-dev:old", "hovorunv2-dev:older"],
    ]
    assert ["buildx", "prune", "--all", "--force"] in scenario.calls()
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "dist").exists()
    assert not (tmp_path / "src/__pycache__").exists()
    assert (tmp_path / ".coveragerc").exists()
    assert (tmp_path / ".venv/keep-or-remove").exists()
    assert (tmp_path / "data/keep-or-remove").exists()
    assert (tmp_path / ".env").read_text() == "synthetic-private-credential"


async def test_invalid_browser_switch_fails_before_build_or_cleanup(tmp_path: Path) -> None:
    """Reject invalid deployment flags before any Docker mutation."""
    scenario = _scenario(tmp_path, "invalid")
    assert await scenario.run("build") != 0
    assert len(scenario.calls()) == 1


async def test_disabled_browser_cannot_be_started_explicitly(tmp_path: Path) -> None:
    """Explicit Compose service selection cannot bypass the deployment browser switch."""
    scenario = _scenario(tmp_path, "False")
    assert await scenario.run("run", "--rm", "worker-browser") != 0
    assert len(scenario.calls()) == 1


async def test_termination_releases_builder_and_lock(tmp_path: Path) -> None:
    """A real SIGTERM unwinds the running Docker child and frees stack ownership."""
    scenario = _scenario(tmp_path, "False")
    scenario.environment["DOCKER_TEST_TERMINATE"] = "1"
    assert await scenario.run("build") == 143
    assert scenario.calls()[-1][:3] == ["buildx", "rm", "--force"]
    del scenario.environment["DOCKER_TEST_TERMINATE"]
    assert await scenario.run("build") == 0


async def test_abandoned_builder_is_removed_before_new_build(tmp_path: Path) -> None:
    """Recover a known builder left by an uncatchable kill without removing unrelated builders."""
    scenario = _scenario(tmp_path, "False")
    project = json.loads(scenario.environment["DOCKER_TEST_CONFIG"])["name"]
    builder = f"{project}-managed-builder"
    scenario.environment["DOCKER_TEST_BUILDERS"] = f"default\nforeign-builder\n{builder}\n"
    assert await scenario.run("build") == 0
    removals = [call for call in scenario.calls() if call[:2] == ["buildx", "rm"]]
    assert removals == [["buildx", "rm", "--force", builder]] * 2
    assert not any(call[:2] == ["image", "rm"] for call in scenario.calls())


async def test_active_stack_lock_blocks_cleanup(tmp_path: Path) -> None:
    """A second process cannot clean the active project's builder or images."""
    scenario = _scenario(tmp_path, "False")
    project = json.loads(scenario.environment["DOCKER_TEST_CONFIG"])["name"]
    lock_path = Path(tempfile.gettempdir()) / f"{project}-{os.getuid()}-docker.lock"
    with lock_path.open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert await scenario.run("clean") != 0
    assert len(scenario.calls()) == 1


async def test_failed_readiness_preserves_previous_images(tmp_path: Path) -> None:
    """Failed Compose readiness prevents all automatic image deletion."""
    scenario = _scenario(tmp_path, "False")
    scenario.environment["DOCKER_TEST_UP_FAIL"] = "1"
    assert await scenario.run("up", "-d", "--no-build") == 8
    assert not any(call[:1] == ["image"] for call in scenario.calls())


async def test_invalidate_runtime_dispatches_valkey_cleanup(tmp_path: Path) -> None:
    """Invalidate runtime state flushes Valkey or removes transient volumes."""
    scenario = _scenario(tmp_path, "False")
    assert await scenario.run("invalidate-runtime") == 0
    calls = scenario.calls()
    assert any("flushall" in call for call in calls)
