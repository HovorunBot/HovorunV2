"""Verify model integrity, recovery, and persistent cache reuse over real HTTP."""

import hashlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hovorunv2.application.translation.ports import TranslationError
from hovorunv2.infrastructure.translation.model_store import ModelFile, ModelHTTPClient, ModelStore
from tests.utils.sync_http import HTTPFixture, HTTPReply


@pytest.mark.parametrize("corrupted", [False, True])
def test_model_provisioning_reuses_verified_files_and_retains_license(tmp_path: Path, corrupted: bool) -> None:
    """Repair corruption and reuse a verified cache without another external request."""
    content = b"synthetic immutable artifact"
    artifact = ModelFile("model.bin", len(content), hashlib.sha256(content).hexdigest())
    directory = tmp_path / "model"
    directory.mkdir()
    if corrupted:
        (directory / artifact.name).write_bytes(b"x" * len(content))
    with (
        HTTPFixture({"/model.bin": HTTPReply(body=content)}) as server,
        ModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        store = ModelStore(directory, http, base_url=server.url(""), artifacts=(artifact,))
        assert store.ensure() == directory
        assert (directory / artifact.name).read_bytes() == content
        assert "Noncommercial" in (directory / "NOTICE.txt").read_text()
        assert (directory / "CC-BY-NC-4.0.txt").is_file()
        assert store.ensure() == directory
        assert len(server.requests) == 1
        assert not list(directory.glob("*.part"))


@pytest.mark.parametrize("content", [b"bad", b"x" * 50])
def test_failed_download_never_publishes_partial_model(tmp_path: Path, content: bytes) -> None:
    """Reject corrupt or oversized assets and recover on the next valid response."""
    valid = b"synthetic model"
    artifact = ModelFile("model.bin", len(valid), hashlib.sha256(valid).hexdigest())
    directory = tmp_path / "model"
    with (
        HTTPFixture({"/model.bin": [HTTPReply(body=content), HTTPReply(body=valid)]}) as server,
        ModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        store = ModelStore(directory, http, base_url=server.url(""), artifacts=(artifact,))
        with pytest.raises(TranslationError):
            store.ensure()
        assert not (directory / artifact.name).exists()
        assert not list(directory.glob("*.part"))
        assert store.ensure() == directory
        assert (directory / artifact.name).read_bytes() == valid


def test_slow_model_stream_expires_and_can_be_retried(tmp_path: Path) -> None:
    """Enforce total time while bytes keep arriving and recover without publishing partial assets."""
    content = b"x" * 100
    artifact = ModelFile("model.bin", len(content), hashlib.sha256(content).hexdigest())
    directory = tmp_path / "model"
    slow = HTTPReply(chunks=tuple(b"x" for _index in content), chunk_delay_seconds=0.02)
    with (
        HTTPFixture({"/model.bin": [slow, HTTPReply(body=content)]}) as server,
        ModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"}), total_timeout=0.1, read_timeout=0.2) as http,
    ):
        store = ModelStore(directory, http, base_url=server.url(""), artifacts=(artifact,))
        started = time.monotonic()
        with pytest.raises(TranslationError):
            store.ensure()
        assert time.monotonic() - started < 1
        assert not (directory / artifact.name).exists()
        assert not list(directory.glob("*.part"))
        assert store.ensure() == directory
        assert (directory / artifact.name).read_bytes() == content


@pytest.mark.subprocess
def test_concurrent_processes_install_one_verified_copy(tmp_path: Path) -> None:
    """Separate worker processes serialize installation and avoid duplicate downloads."""
    content = b"synthetic shared model"
    directory = tmp_path / "model"
    script = """
import sys
from pathlib import Path
from hovorunv2.infrastructure.translation.model_store import ModelFile, ModelHTTPClient, ModelStore
with ModelHTTPClient(allowed_hosts=frozenset({'127.0.0.1'})) as http:
    ModelStore(Path(sys.argv[1]), http, base_url=sys.argv[2], artifacts=(
        ModelFile('model.bin', int(sys.argv[3]), sys.argv[4]),
    )).ensure()
"""
    with HTTPFixture({"/model.bin": HTTPReply(body=content, delay_seconds=0.05)}) as server:
        arguments = [
            sys.executable,
            "-c",
            script,
            str(directory),
            server.url(""),
            str(len(content)),
            hashlib.sha256(content).hexdigest(),
        ]
        processes = [subprocess.Popen(arguments) for _index in range(2)]  # noqa: S603
        try:
            assert [process.wait(timeout=15) for process in processes] == [0, 0]
        finally:
            _stop_processes(processes)
        assert len(server.requests) == 1
        assert (directory / "model.bin").read_bytes() == content


def _stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait()
