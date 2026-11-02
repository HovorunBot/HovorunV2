"""Build optional Docker roles without retained caches and clean application artifacts."""

# Standalone operator script; this directory is not an application package.
# ruff: noqa: INP001

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STACK_LABEL = "io.hovorunv2.stack"
BUILD_ROLES = ("bot", "worker-browser")
TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
FALSE_VALUES = frozenset({"false", "0", "no", "off"})
ARTIFACT_DIRECTORIES = ("build", "dist", ".pytest_cache", ".ruff_cache", ".import_linter_cache", "htmlcov")
SOURCE_DIRECTORIES = ("src", "tests", "alembic", "scripts")


class DockerStack:
    """Own Compose selection, temporary builders and project-scoped image cleanup."""

    def __init__(self, environment: str, env_file: str, version: str) -> None:
        self._environment = os.environ | {"APP_ENV_FILE": env_file, "APP_VERSION": version, "COMPOSE_PROFILES": ""}
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
        subprocess.run(command, cwd=PROJECT_ROOT, env=self._environment, check=True)  # noqa: S603

    def _capture(self, command: list[str]) -> str:
        return subprocess.run(  # noqa: S603
            command,
            cwd=PROJECT_ROOT,
            env=self._environment,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout

    def build(self) -> None:
        """Build selected images and always remove the dedicated builder and its cache."""
        self.clean_images()
        builder = f"{self._project}-build-{uuid4().hex}"
        try:
            self._run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container"])
            for role in BUILD_ROLES:
                if role == "worker-browser" and not self._browser_enabled:
                    continue
                service = self._configuration["services"][role]
                self._run(
                    [
                        "docker",
                        "buildx",
                        "build",
                        "--builder",
                        builder,
                        "--load",
                        "--no-cache",
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
        finally:
            self._run(["docker", "buildx", "rm", "--force", builder])

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
        self._run([*self._compose, *profiles, *arguments])
        if starting:
            self.clean_images()

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
        """Remove project artifacts and the selected Docker builder's unused cache."""
        self._run([*self._compose, "--profile", "*", "rm", "--force"])
        self.clean_images()
        self._run(["docker", "buildx", "prune", "--all", "--force"])
        _clean_local_artifacts()


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


def main() -> None:
    """Dispatch the selected stack command without printing resolved credentials."""
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
    if command == ["build"]:
        stack.build()
        return
    if command == ["clean"]:
        stack.clean()
        return
    stack.compose(command)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
