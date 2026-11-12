"""Verify speech model storage, integrity checks, and cache reuse."""

import hashlib
import traceback
from pathlib import Path

import pytest

from hovorunv2.application.speech.contracts import SpeechFailure, SpeechRecognitionError
from hovorunv2.infrastructure.speech.model_store import (
    ModelFile,
    SpeechModelHTTPClient,
    SpeechModelStore,
)
from tests.utils.sync_http import HTTPFixture, HTTPReply


def test_speech_model_provisioning_verifies_hash_and_reuses_cache(tmp_path: Path) -> None:
    """Store saves verified file and serves subsequent requests from disk."""
    content = b"synthetic speech model weights"
    artifact = ModelFile("ggml-small-q8_0.bin", len(content), hashlib.sha256(content).hexdigest())
    directory = tmp_path / "models"

    with (
        HTTPFixture({"/ggml-small-q8_0.bin": HTTPReply(body=content)}) as server,
        SpeechModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        store = SpeechModelStore(directory, http, base_url=server.url(""), artifacts=(artifact,))
        assert store.ensure() == directory
        assert (directory / artifact.name).read_bytes() == content
        assert (directory / "NOTICE.txt").is_file()
        assert (directory / "MIT.txt").is_file()

        # Second ensure reuses file without re-downloading
        assert store.ensure() == directory
        assert len(server.requests) == 1
        assert not list(directory.glob("*.part"))


@pytest.mark.parametrize("corrupted", [b"bad", b"x" * 100])
def test_speech_model_rejects_corrupted_download(tmp_path: Path, corrupted: bytes) -> None:
    """Oversized or corrupted download raises SpeechRecognitionError and leaves no partial files."""
    valid = b"synthetic valid model"
    artifact = ModelFile("ggml-small-q8_0.bin", len(valid), hashlib.sha256(valid).hexdigest())
    directory = tmp_path / "models"

    with (
        HTTPFixture({"/ggml-small-q8_0.bin": [HTTPReply(body=corrupted), HTTPReply(body=valid)]}) as server,
        SpeechModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        store = SpeechModelStore(directory, http, base_url=server.url(""), artifacts=(artifact,))
        with pytest.raises(SpeechRecognitionError) as exc_info:
            store.ensure()
        assert exc_info.value.reason == SpeechFailure.MODEL_UNAVAILABLE
        assert not (directory / artifact.name).exists()
        assert not list(directory.glob("*.part"))

        # Recovers on subsequent valid download
        assert store.ensure() == directory
        assert (directory / artifact.name).read_bytes() == valid


def test_preparation_writes_only_inside_its_model_volume(tmp_path: Path) -> None:
    """Preparation works when the application root is read-only and only the model directory is writable."""
    directory = tmp_path / "models"
    directory.mkdir()
    content = b"synthetic public asset"
    artifact = ModelFile("ggml-small-q8_0.bin", len(content), hashlib.sha256(content).hexdigest())
    with (
        HTTPFixture({"/ggml-small-q8_0.bin": HTTPReply(body=content)}) as server,
        SpeechModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        tmp_path.chmod(0o555)
        try:
            store = SpeechModelStore(directory, http, base_url=server.url(""), artifacts=(artifact,))
            assert store.ensure() == directory
            assert store.require_ready() == directory
        finally:
            tmp_path.chmod(0o700)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["models"]


def test_preparation_reports_filesystem_failure_without_exposing_paths(tmp_path: Path) -> None:
    """Give operators an actionable permission diagnosis without exposing raw filesystem errors."""
    directory = tmp_path / "private-location"
    directory.mkdir()
    with SpeechModelHTTPClient() as http:
        directory.chmod(0o555)
        try:
            with pytest.raises(SpeechRecognitionError) as failure:
                SpeechModelStore(directory, http).ensure()
        finally:
            directory.chmod(0o700)
    diagnostic = "".join(traceback.format_exception_only(failure.value))
    assert "EACCES" in diagnostic
    assert "writable" in diagnostic
    assert str(directory) not in diagnostic
