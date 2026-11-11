"""Provision pinned speech model assets atomically without buffering model weights."""

import fcntl
import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from hovorunv2.application.jobs.http_ports import HTTPTransportError
from hovorunv2.application.speech.contracts import SpeechFailure, SpeechRecognitionError
from hovorunv2.infrastructure.http_transport.native_http import HTTP_CHUNK_BYTES, NativeHTTPClient

SPEECH_MODEL_REPOSITORY = "ggerganov/whisper.cpp"
SPEECH_MODEL_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
SPEECH_MODEL_BASE_URL = f"https://huggingface.co/{SPEECH_MODEL_REPOSITORY}/resolve/{SPEECH_MODEL_REVISION}"
MODEL_DOWNLOAD_SECONDS = 90
MODEL_LOCK_SECONDS = 10
MODEL_LOCK_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class ModelFile:
    """Describe one immutable, size-bounded speech model artifact."""

    name: str
    size: int
    sha256: str


SPEECH_MODEL_FILES = (
    ModelFile(
        "ggml-small-q8_0.bin",
        264464607,
        "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f",
    ),
)


class SpeechModelDownloadPort(Protocol):
    """Stream a public model artifact to a caller-owned temporary file."""

    def download_to(self, url: str, destination: Path, artifact: ModelFile) -> None:
        """Write and validate the artifact or raise SpeechRecognitionError."""
        ...


class SpeechModelHTTPClient(NativeHTTPClient):
    """Reuse bounded public-destination HTTP policy while streaming large speech model files."""

    def download_to(self, url: str, destination: Path, artifact: ModelFile) -> None:
        """Stream a pinned file and require its exact size and SHA-256 digest.

        Args:
            url: Public immutable model artifact URL.
            destination: Temporary output file owned by model provisioning.
            artifact: Expected artifact identity and byte limit.

        Raises:
            SpeechRecognitionError: If the response, byte count, digest, or deadline is invalid.
        """
        started = time.monotonic()
        try:
            with self._request("GET", url) as response:
                response.raise_for_status()
                self._write_response(response, destination, artifact, started)
        except requests.RequestException, HTTPTransportError, OSError:
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE) from None

    def _write_response(
        self, response: requests.Response, destination: Path, artifact: ModelFile, started: float
    ) -> None:
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as output:
            for chunk in response.iter_content(HTTP_CHUNK_BYTES):
                size += len(chunk)
                if size > artifact.size or time.monotonic() - started > self._total_timeout:
                    raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE)
                digest.update(chunk)
                output.write(chunk)
        if size != artifact.size or digest.hexdigest() != artifact.sha256:
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE)


class SpeechModelStore:
    """Serialize speech model provisioning across workers and publish only verified complete artifacts."""

    def __init__(
        self,
        model_dir: Path,
        http: SpeechModelDownloadPort,
        *,
        base_url: str = SPEECH_MODEL_BASE_URL,
        artifacts: tuple[ModelFile, ...] = SPEECH_MODEL_FILES,
    ) -> None:
        self._directory = model_dir
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._artifacts = artifacts

    def ensure(self) -> Path:
        """Return a complete pinned model directory, retaining attribution alongside weights.

        Returns:
            Directory containing verified model artifacts and their license notices.

        Raises:
            SpeechRecognitionError: If locking, provisioning, or local filesystem operations fail.
        """
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with (self._directory.parent / (self._directory.name + ".lock")).open("a") as lock:
                self._acquire_lock(lock.fileno())
                for artifact in self._artifacts:
                    self._ensure_file(artifact)
                self._install_notices()
        except OSError:
            raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE) from None
        return self._directory

    def _acquire_lock(self, descriptor: int) -> None:
        deadline = time.monotonic() + MODEL_LOCK_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SpeechRecognitionError(SpeechFailure.MODEL_UNAVAILABLE) from None
                time.sleep(MODEL_LOCK_POLL_SECONDS)
            else:
                return

    def _ensure_file(self, artifact: ModelFile) -> None:
        destination = self._directory / artifact.name
        if destination.is_file() and destination.stat().st_size == artifact.size:
            with destination.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == artifact.sha256:
                    return
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            self._http.download_to(f"{self._base_url}/{artifact.name}", partial, artifact)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)

    def _install_notices(self) -> None:
        source = Path(__file__).parent / "licenses"
        for name in ("MIT.txt", "NOTICE.txt"):
            temporary = self._directory / (name + ".part")
            try:
                shutil.copyfile(source / name, temporary)
                temporary.replace(self._directory / name)
            finally:
                temporary.unlink(missing_ok=True)
