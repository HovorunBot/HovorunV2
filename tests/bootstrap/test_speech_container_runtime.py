"""Exercise speech prefork startup and filesystem boundaries in the configured container runtime."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_TIMEOUT_SECONDS = 60
PREFORK_PROBE = """
import errno
import json
from os import getpid
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

from billiard.pool import Pool

with Pool(processes=1) as pool:
    child_pid = pool.apply_async(getpid).get(timeout=10)
with TemporaryDirectory() as directory:
    Path(directory, 'runtime-probe').write_bytes(b'synthetic operational marker')

readonly = {}
for name, path in [('root', '/app/.runtime-probe'), ('models', '/app/data/models/speech/.runtime-probe')]:
    try:
        Path(path).touch()
    except OSError as error:
        readonly[name] = error.errno == errno.EROFS
    else:
        Path(path).unlink()
        readonly[name] = False
mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
temporary_directory = Path(gettempdir()).resolve()
temporary_mount = max(
    (fields for fields in mounts if temporary_directory.is_relative_to(fields[1])),
    key=lambda fields: len(fields[1]),
)
temporary_filesystems = [temporary_mount[2]]
print(json.dumps({'parent_pid': getpid(), 'child_pid': child_pid, 'readonly': readonly,
                  'temporary_filesystems': temporary_filesystems}))
"""

pytestmark = pytest.mark.subprocess


@pytest.mark.parametrize("environment", ["prod", "dev"])
def test_speech_prefork_runs_with_private_temporary_storage(tmp_path: Path, environment: str) -> None:
    """Start a real prefork child while application files and prepared models remain read-only."""
    image = os.environ.get("HOVORUN_SPEECH_TEST_IMAGE")
    if image is None:
        pytest.skip("Set HOVORUN_SPEECH_TEST_IMAGE to exercise the built speech container")
    project = f"speech-runtime-test-{uuid4().hex}"
    environment_file = tmp_path / "runtime.env"
    environment_file.write_text("SPEECH_ENABLED=False\nBROWSER_ENABLED=False\n")
    override = tmp_path / "runtime.yml"
    override.write_text(yaml.safe_dump({"services": {"worker-speech": {"image": image, "build": None}}}))
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(environment_file),
        "--file",
        str(PROJECT_ROOT / f"docker-compose.{environment}.yml"),
        "--file",
        str(override),
        "--profile",
        "speech",
    ]
    environment_values = os.environ | {
        "APP_ENV_FILE": str(environment_file),
        "BOT_TOKEN": "123456:synthetic",
        "CACHE_ENCRYPTION_KEY": "synthetic-test-key",
        "DATABASE_URL": "postgresql+psycopg://synthetic:synthetic@postgres/synthetic",
        "POSTGRES_DB": "synthetic",
        "POSTGRES_USER": "synthetic",
        "POSTGRES_PASSWORD": "synthetic",
    }
    try:
        result = subprocess.run(  # noqa: S603
            [*command, "run", "--rm", "--no-deps", "-T", "worker-speech", "python", "-c", PREFORK_PROBE],
            env=environment_values,
            check=False,
            capture_output=True,
            text=True,
            timeout=CONTAINER_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed["child_pid"] != observed["parent_pid"]
        assert observed["readonly"] == {"root": True, "models": True}
        assert observed["temporary_filesystems"] == ["tmpfs"]
    finally:
        subprocess.run(  # noqa: S603
            [*command, "down", "--volumes", "--remove-orphans"],
            env=environment_values,
            check=True,
            capture_output=True,
            timeout=CONTAINER_TIMEOUT_SECONDS,
        )
