"""Provision pinned translation assets atomically without buffering model weights."""

import fcntl
import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from hovorunv2.application.jobs.http_ports import HTTPTransportError
from hovorunv2.application.translation.ports import TranslationError
from hovorunv2.infrastructure.http_transport.native_http import HTTP_CHUNK_BYTES, NativeHTTPClient

MODEL_REPOSITORY = "auralmira/m2m100-418M-ct2-int8"
MODEL_REVISION = "e205afefce2fd6933a1dca3b92b5063894f2f2bb"
MODEL_BASE_URL = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}"
LEGACY_MODEL_DIR_NAME = "nllb-200-distilled-600M-int8"
MODEL_DOWNLOAD_SECONDS = 45
MODEL_LOCK_SECONDS = 5
MODEL_LOCK_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class ModelFile:
    """Describe one immutable, size-bounded model artifact."""

    name: str
    size: int
    sha256: str


MODEL_FILES = (
    ModelFile("config.json", 223, "8f6496adfc930cbfecbe8281112197705c488fab47d34b4829b06d7f478909af"),
    ModelFile("model.bin", 490667752, "137949450f9cf691f3c92084f14123e82d05ecb23ac3cd688308ce1a95214572"),
    ModelFile("sentencepiece.bpe.model", 2423393, "d8f7c76ed2a5e0822be39f0a4f95a55eb19c78f4593ce609e2edbc2aea4d380a"),
    ModelFile("shared_vocabulary.json", 2796509, "7eb5d0ff184c6095c7c10f9911c0aea492250abd12854f9c3d787c64b1c6397e"),
)


class ModelDownloadPort(Protocol):
    """Stream a public model artifact to a caller-owned temporary file."""

    def download_to(self, url: str, destination: Path, artifact: ModelFile) -> None:
        """Write and validate the artifact or raise TranslationError."""
        ...


class ModelHTTPClient(NativeHTTPClient):
    """Reuse bounded public-destination HTTP policy while streaming large model files."""

    def download_to(self, url: str, destination: Path, artifact: ModelFile) -> None:
        """Stream a pinned file and require its exact size and SHA-256 digest.

        Args:
            url: Public immutable model artifact URL.
            destination: Temporary output file owned by model provisioning.
            artifact: Expected artifact identity and byte limit.

        Raises:
            TranslationError: If the response, byte count, digest, or deadline is invalid.
        """
        started = time.monotonic()
        try:
            with self._request("GET", url) as response:
                response.raise_for_status()
                self._write_response(response, destination, artifact, started)
        except requests.RequestException, HTTPTransportError, OSError:
            msg = "Translation model download failed"
            raise TranslationError(msg) from None

    def _write_response(
        self, response: requests.Response, destination: Path, artifact: ModelFile, started: float
    ) -> None:
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as output:
            while True:
                if time.monotonic() - started > self._total_timeout:
                    msg = "Translation model download exceeded bounds"
                    raise TranslationError(msg)
                chunk = response.raw.read1(HTTP_CHUNK_BYTES, decode_content=True)
                size += len(chunk)
                if size > artifact.size or time.monotonic() - started > self._total_timeout:
                    msg = "Translation model download exceeded bounds"
                    raise TranslationError(msg)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
        if size != artifact.size or digest.hexdigest() != artifact.sha256:
            msg = "Translation model integrity check failed"
            raise TranslationError(msg)


class ModelStore:
    """Serialize provisioning across workers and publish only verified complete artifacts."""

    def __init__(
        self,
        model_dir: Path,
        http: ModelDownloadPort,
        *,
        base_url: str = MODEL_BASE_URL,
        artifacts: tuple[ModelFile, ...] = MODEL_FILES,
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
            TranslationError: If locking, provisioning, or local filesystem operations fail.
        """
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with (self._directory.parent / (self._directory.name + ".lock")).open("a") as lock:
                self._acquire_lock(lock.fileno())
                for artifact in self._artifacts:
                    self._ensure_file(artifact)
                self._install_notices()
                self._cleanup_legacy_model()
        except OSError:
            msg = "Translation model storage unavailable"
            raise TranslationError(msg) from None
        return self._directory

    def _acquire_lock(self, descriptor: int) -> None:
        deadline = time.monotonic() + MODEL_LOCK_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    msg = "Translation model provisioning is busy"
                    raise TranslationError(msg) from None
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

    def _cleanup_legacy_model(self) -> None:
        if self._directory.name == LEGACY_MODEL_DIR_NAME:
            return
        legacy_dir = self._directory.parent / LEGACY_MODEL_DIR_NAME
        if legacy_dir.is_dir():
            shutil.rmtree(legacy_dir, ignore_errors=True)
        (self._directory.parent / f"{LEGACY_MODEL_DIR_NAME}.lock").unlink(missing_ok=True)
