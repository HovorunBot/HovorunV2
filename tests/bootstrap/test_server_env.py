"""Server secrets differ from committed development defaults and never overwrite existing configuration."""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.subprocess]


async def test_server_environment_generation_is_private_and_preserves_existing_keys(tmp_path: Path) -> None:
    """Run the actual operator script against a disposable project layout, without reading the real .env."""
    scripts = tmp_path / "scripts"
    await asyncio.to_thread(scripts.mkdir)
    script = scripts / "init-server-env.py"
    await asyncio.to_thread(shutil.copyfile, PROJECT_ROOT / "scripts/init-server-env.py", script)
    await asyncio.to_thread(shutil.copyfile, PROJECT_ROOT / "example.env", tmp_path / "example.env")
    process = await asyncio.create_subprocess_exec(sys.executable, str(script), stdout=asyncio.subprocess.PIPE)
    output, _error = await process.communicate()
    assert process.returncode == 0
    path = tmp_path / ".env"
    first = await asyncio.to_thread(path.read_text)
    values = dict(line.split("=", 1) for line in first.splitlines() if line and not line.startswith("#"))
    password = values["POSTGRES_PASSWORD"]
    assert password != values["POSTGRES_USER"]
    assert len(password) >= 32
    assert password in values["DATABASE_URL"]
    assert "@postgres:5432/hovorun" in values["DATABASE_URL"]
    assert values["CACHE_ENCRYPTION_KEY"]
    assert "OPENROUTER" not in values
    assert password.encode() not in output
    assert values["CACHE_ENCRYPTION_KEY"].encode() not in output
    assert (await asyncio.to_thread(path.stat)).st_mode & 0o777 == 0o600
    process = await asyncio.create_subprocess_exec(sys.executable, str(script), stdout=asyncio.subprocess.PIPE)
    await process.communicate()
    assert process.returncode == 0
    assert await asyncio.to_thread(path.read_text) == first
