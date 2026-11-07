"""Build Docker roles with persistent BuildKit caching and clean application artifacts."""

# Standalone operator script; this directory is not an application package.
# ruff: noqa: INP001

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from types import FrameType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STACK_LABEL = "io.hovorunv2.stack"
CHILD_STOP_SECONDS = 10
BUILD_ROLES = (
    "migrate",
    "bot",
    "worker-operations",
    "worker-media",
    "worker-browser",
)
TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
FALSE_VALUES = frozenset({"false", "0", "no", "off"})
ARTIFACT_DIRECTORIES = ("build", "dist", ".pytest_cache", ".ruff_cache", ".import_linter_cache", "htmlcov")
SOURCE_DIRECTORIES = ("src", "tests", "alembic", "scripts")


def _image_tag() -> str:
    """Return a content-addressed image tag from the current commit."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return f"g{result.stdout.strip()}" if result.returncode == 0 and result.stdout.strip() else "local"


class DockerStack:
    """Own Compose selection, temporary builders and project-scoped image cleanup."""

    def __init__(self, environment: str, env_file: str, version: str) -> None:
        self._environment = os.environ | {
            "APP_ENV_FILE": env_file,
            "APP_VERSION": version,
            "APP_TAG": _image_tag(),
            "COMPOSE_PROFILES": "",
            "DOCKER_BUILDKIT": "1",
        }
        self._compose = [
            "docker",
            "compose",
            "--env-file",
            env_file if Path(env_file).exists() else "/dev/null",
            "--file",
            f"docker-compose.{environment}.yml",
        ]
        configuration = self._capture([*self._compose, "--profile", "*", "config", "--format", "json"])
        self._configuration = json.loads(configuration)
        raw = str(self._configuration["services"]["worker-media"]["environment"]["BROWSER_ENABLED"]).lower()
        if raw not in TRUE_VALUES | FALSE_VALUES:
            msg = "BROWSER_ENABLED must be a boolean (True or False)."
            raise ValueError(msg)
        self._browser_enabled = raw in TRUE_VALUES
        self._project = self._configuration["name"]

    def _run(self, command: list[str]) -> None:
        with subprocess.Popen(command, cwd=PROJECT_ROOT, env=self._environment) as process:  # noqa: S603
            try:
                result = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=CHILD_STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            if result:
                raise subprocess.CalledProcessError(result, command)

    def _capture(self, command: list[str]) -> str:
        return subprocess.run(  # noqa: S603
            command,
            cwd=PROJECT_ROOT,
            env=self._environment,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout

    def _image_exists(self, image: str) -> bool:
        """Check whether a Docker image already exists locally in the daemon."""
        return (
            subprocess.run(  # noqa: S603
                ["docker", "image", "inspect", image],  # noqa: S607
                cwd=PROJECT_ROOT,
                env=self._environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )

    def _roles_to_build(self, *, force: bool) -> list[str]:
        """Determine which configured image roles require building.

        Args:
            force: Rebuild images even when target tags already exist locally.

        Returns:
            List of service role names that must be built.
        """
        required_roles = [role for role in BUILD_ROLES if not (role == "worker-browser" and not self._browser_enabled)]
        if force:
            return list(required_roles)
        return [
            role for role in required_roles if not self._image_exists(self._configuration["services"][role]["image"])
        ]

    def build(self, *, force: bool = False) -> None:
        """Build selected images with persistent BuildKit caching.

        Args:
            force: Rebuild images without cache even when target tags already exist locally.
        """
        roles = self._roles_to_build(force=force)
        if not roles:
            sys.stdout.write("All required images are already built. Use FORCE=1 to rebuild.\n")
            return
        for role in roles:
            service = self._configuration["services"][role]
            command = [
                "docker",
                "buildx",
                "build",
                "--load",
            ]
            if force:
                command.append("--no-cache")
            command.extend(
                [
                    "--target",
                    service["build"]["target"],
                    "--tag",
                    service["image"],
                    "--label",
                    f"{STACK_LABEL}={self._project}",
                    "--build-arg",
                    f"APP_VERSION={self._environment['APP_VERSION']}",
                    ".",
                ]
            )
            self._run(command)

    def compose(self, arguments: list[str]) -> None:
        """Run Compose with browser selection and clean obsolete images after startup.

        Args:
            arguments: Compose command and its options.

        Raises:
            ValueError: If an explicit service command would start a disabled browser worker.
        """
        if (
            not self._browser_enabled
            and arguments[0] in {"build", "create", "run", "up", "start", "restart"}
            and "worker-browser" in arguments
        ):
            msg = "Set BROWSER_ENABLED=True before building or starting worker-browser."
            raise ValueError(msg)
        profiles = ["--profile", "browser"] if self._browser_enabled else []
        if arguments[0] in {"down", "stop", "rm"}:
            profiles = ["--profile", "*"]
        starting = arguments[0] == "up" and "--no-build" in arguments
        if starting and not self._browser_enabled:
            self._run([*self._compose, "--profile", "browser", "rm", "--stop", "--force", "worker-browser"])
        if starting:
            arguments = [*arguments, "--wait", "--wait-timeout", "180"]
        self._run([*self._compose, *profiles, *arguments])
        if starting:
            self.clean_images()

    def execute(self, command: list[str]) -> None:
        """Serialize mutations so cleanup and builds cannot conflict.

        Args:
            command: Requested stack command and arguments.
        """
        if command[0] not in {
            "build",
            "clean",
            "invalidate-runtime",
            "up",
            "down",
            "stop",
            "rm",
            "run",
            "start",
            "restart",
        }:
            self.compose(command)
            return
        lock_path = Path(tempfile.gettempdir()) / f"{self._project}-{os.getuid()}-docker.lock"
        with lock_path.open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._execute_locked(command)

    def _execute_locked(self, command: list[str]) -> None:
        """Dispatch one operation after exclusive ownership has been established."""
        if command[0] == "build":
            force = any(argument in {"--force", "-f"} for argument in command[1:]) or (
                os.environ.get("FORCE", "").lower() in TRUE_VALUES
            )
            self.build(force=force)
            return
        if command == ["clean"]:
            self.clean()
            return
        if command == ["invalidate-runtime"]:
            self.invalidate_runtime()
            return
        self.compose(command)

    def clean_images(self) -> None:
        """Remove unused application images while preserving all container references."""
        repositories = {self._configuration["services"][role]["image"].rsplit(":", 1)[0] for role in BUILD_ROLES}
        images = self._capture(["docker", "image", "ls", "--no-trunc", "--format", "json"])
        references: dict[str, list[str]] = {}
        foreign_images: set[str] = set()
        for line in images.splitlines():
            record = json.loads(line)
            image_id = record["ID"]
            repository = record["Repository"]
            if repository in repositories:
                references.setdefault(image_id, []).append(f"{repository}:{record['Tag']}")
            elif repository != "<none>":
                foreign_images.add(image_id)
        labelled = self._capture(
            [
                "docker",
                "image",
                "ls",
                "--no-trunc",
                "--quiet",
                "--filter",
                f"label={STACK_LABEL}={self._project}",
            ]
        )
        candidates = set(references) | set(labelled.splitlines())
        for image_id in sorted(candidates - foreign_images):
            containers = self._capture(
                [
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"ancestor={image_id}",
                ]
            )
            if containers.strip():
                continue
            self._run(["docker", "image", "rm", *references.get(image_id, [image_id])])

    def clean(self) -> None:
        """Remove project artifacts, unused images, and prune builder cache."""
        self._run([*self._compose, "--profile", "*", "rm", "--force"])
        self.clean_images()
        self._run(["docker", "image", "prune", "-f"])
        self._run(["docker", "builder", "prune", "-f"])
        _clean_local_artifacts()

    def invalidate_runtime(self) -> None:
        """Invalidate transient Valkey runtime state without affecting persistent database volumes."""
        flushed = False
        res = subprocess.run(  # noqa: S603
            [*self._compose, "exec", "-T", "valkey", "valkey-cli", "flushall"],
            cwd=PROJECT_ROOT,
            env=self._environment,
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            flushed = True
        if not flushed:
            for volume_suffix in ("valkey_data", "beat_state"):
                volume_name = f"{self._project}_{volume_suffix}"
                with suppress(subprocess.CalledProcessError):
                    self._run(["docker", "volume", "rm", "-f", volume_name])


def _clean_local_artifacts() -> None:
    """Remove generated project artifacts while preserving environments and application data."""
    paths = [PROJECT_ROOT / name for name in ARTIFACT_DIRECTORIES]
    paths.append(PROJECT_ROOT / ".coverage")
    paths.extend(PROJECT_ROOT.glob(".coverage.*"))
    for directory in SOURCE_DIRECTORIES:
        paths.extend((PROJECT_ROOT / directory).rglob("__pycache__"))
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _terminate(signum: int, frame: FrameType | None) -> None:
    """Unwind the operation on termination so child and builder cleanup can finish.

    Args:
        signum: Received termination signal.
        frame: Interrupted interpreter frame.

    Raises:
        SystemExit: To unwind the active operation with a signal-derived exit status.
    """
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def main() -> None:
    """Dispatch the selected stack command without printing resolved credentials."""
    signal.signal(signal.SIGTERM, _terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("dev", "prod"), required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("A stack command is required.")
    stack = DockerStack(arguments.environment, arguments.env_file, arguments.version)
    stack.execute(command)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
