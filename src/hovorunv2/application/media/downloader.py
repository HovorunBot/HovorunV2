"""Application service for native synchronous media downloading."""

import html
import logging
import tempfile
import time
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from threading import current_thread, main_thread

import yt_dlp

from hovorunv2.application.jobs.http_ports import (
    DestinationPolicyPort,
    HTTPFailure,
    HTTPTransportError,
    SyncHTTPPort,
)
from hovorunv2.application.media.backend_logging import MediaBackendLogger
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaDownloadError, MediaExtractionError, MediaFailure
from hovorunv2.application.media.limits import MAX_SOURCE_MEDIA_BYTES
from hovorunv2.application.media.payloads import MediaItem
from hovorunv2.application.media.video_processing import prepare_video
from hovorunv2.utils.operation_timeout import OperationTimeout

logger = logging.getLogger(__name__)


MAX_MEDIA_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
HLS_MANIFEST_SIZE_THRESHOLD = 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
BATCH_TIMEOUT_SECONDS = 90
DEFAULT_DOWNLOAD_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def is_hls_manifest(content: bytes) -> bool:
    """Return whether the content is a small HLS manifest."""
    return len(content) < HLS_MANIFEST_SIZE_THRESHOLD and "#EXTM3U" in content.decode("utf-8", errors="ignore")


def _enforce_download_limit(event: dict[str, object], *, max_bytes: int) -> None:
    downloaded = event.get("downloaded_bytes")
    if isinstance(downloaded, int) and downloaded > max_bytes:
        msg = "Media download exceeds the configured byte bound."
        raise yt_dlp.utils.DownloadError(msg)
    total = event.get("total_bytes")
    if isinstance(total, int) and total > max_bytes:
        msg = "Media download exceeds the configured byte bound."
        raise yt_dlp.utils.DownloadError(msg)
    total_estimate = event.get("total_bytes_estimate")
    if isinstance(total_estimate, int | float) and total_estimate > max_bytes:
        msg = "Media download exceeds the configured byte bound."
        raise yt_dlp.utils.DownloadError(msg)


class _DownloadBudget:
    """Bound combined video/audio transfers instead of granting each stream a full allowance."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._downloaded: dict[str, int] = {}

    def check(self, event: dict[str, object]) -> None:
        """Reject cumulative transfer bytes above the source limit."""
        _enforce_download_limit(event, max_bytes=self._max_bytes)
        filename, downloaded = event.get("filename"), event.get("downloaded_bytes")
        if not isinstance(filename, str) or not isinstance(downloaded, int):
            return
        self._downloaded[filename] = max(self._downloaded.get(filename, 0), downloaded)
        if sum(self._downloaded.values()) > self._max_bytes:
            msg = "Media download exceeds the configured byte bound."
            raise yt_dlp.utils.DownloadError(msg)


def _require_content(content: bytes, max_bytes: int = MAX_MEDIA_BYTES) -> bytes:
    if len(content) > max_bytes:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    if not content:
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    return content


def _read_downloaded_file(output: Path, root: Path, max_bytes: int) -> bytes:
    if not output.resolve().is_relative_to(root.resolve()) or not output.is_file():
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    if output.stat().st_size > max_bytes:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    with output.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    return _require_content(content, max_bytes)


def _download_options(
    root: Path, max_bytes: int, *, timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS
) -> dict[str, object]:
    bounded_timeout = max(1, int(timeout_seconds))
    return {
        "format": '(bestvideo+bestaudio/best)[protocol~="^(https?|m3u8)"]',
        "merge_output_format": "mp4",
        "outtmpl": str(root / "downloaded.%(ext)s"),
        "quiet": True,
        "logger": MediaBackendLogger(),
        "no_warnings": True,
        "socket_timeout": min(DOWNLOAD_TIMEOUT_SECONDS, bounded_timeout),
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "skip_unavailable_fragments": False,
        "noplaylist": True,
        "enable_file_urls": False,
        "max_filesize": max_bytes,
        "progress_hooks": [_DownloadBudget(max_bytes).check],
    }


def _download_to_file(
    url: str, root: Path, max_bytes: int, *, timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS
) -> Path:
    try:
        with yt_dlp.YoutubeDL(_download_options(root, max_bytes, timeout_seconds=timeout_seconds)) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise MediaDownloadError(MediaFailure.UNUSABLE)
            filename = ydl.prepare_filename(info)
            if not isinstance(filename, str):
                raise MediaDownloadError(MediaFailure.UNUSABLE)
            return Path(filename)
    except yt_dlp.utils.DownloadError as error:
        if "byte bound" in str(error) or "max_filesize" in str(error).lower():
            raise MediaDownloadError(MediaFailure.TOO_LARGE) from None
        raise


def download_ytdlp_bytes(
    url: str,
    *,
    destination_policy: DestinationPolicyPort,
    max_bytes: int = MAX_SOURCE_MEDIA_BYTES,
    deadline: ExtractionDeadline | None = None,
) -> bytes:
    """Require bounded output while owning and cleaning every temporary backend artifact.

    Args:
        url: Media URL to download.
        destination_policy: Mandatory policy validating public network destinations.
        max_bytes: Upper bound on the downloaded payload.
        deadline: Optional extraction deadline bounding execution wall-clock time.

    Returns:
        Downloaded content bytes.

    Raises:
        MediaDownloadError: If the backend fails or the output exceeds the bound.
    """
    try:
        destination_policy.validate_url(url)
    except HTTPTransportError as error:
        code = MediaFailure.TOO_LARGE if error.code == HTTPFailure.TOO_LARGE else MediaFailure.UNAVAILABLE
        raise MediaDownloadError(code) from None

    try:
        allowance = deadline.narrow(DOWNLOAD_TIMEOUT_SECONDS) if deadline is not None else None
    except MediaExtractionError, ValueError:
        raise MediaDownloadError(MediaFailure.UNAVAILABLE) from None

    expires_at = allowance.expires_at if allowance is not None else (time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS)
    remaining = allowance.remaining() if allowance is not None else DOWNLOAD_TIMEOUT_SECONDS
    if remaining <= 0:
        raise MediaDownloadError(MediaFailure.UNAVAILABLE)

    guard = OperationTimeout(expires_at) if current_thread() is main_thread() else nullcontext()
    try:
        with guard, tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            return _read_downloaded_file(
                _download_to_file(url, root, max_bytes, timeout_seconds=remaining), root, max_bytes
            )
    except MediaDownloadError:
        logger.warning("Media backend download failed")
        raise
    except yt_dlp.utils.YoutubeDLError, OSError, ValueError, TimeoutError:
        logger.warning("Media backend download failed")
        raise MediaDownloadError from None


class NativeMediaDownloader:
    """Synchronous worker downloads sharing HLS detection and bounded yt-dlp fallback."""

    def __init__(self, http: SyncHTTPPort, destination_policy: DestinationPolicyPort) -> None:
        self._http = http
        self._destination_policy = destination_policy

    def _direct_download(self, url: str) -> bytes:
        try:
            content = self._http.download_bytes(url, headers=DEFAULT_DOWNLOAD_HEADERS)
        except HTTPTransportError as error:
            code = MediaFailure.TOO_LARGE if error.code == HTTPFailure.TOO_LARGE else MediaFailure.UNAVAILABLE
            raise MediaDownloadError(code) from None
        return _require_content(content, MAX_SOURCE_MEDIA_BYTES)

    def download(self, url: str, *, is_video: bool = False, deadline: ExtractionDeadline | None = None) -> bytes:
        """Require direct bytes or fall back to the video-only strategy.

        Args:
            url: Media URL to download.
            is_video: Whether the media is a video.
            deadline: Optional shared deadline bounding the download.

        Returns:
            Downloaded content bytes.

        Raises:
            MediaDownloadError: If the download fails and no fallback applies.
        """
        logger.info("Worker starting media download")
        try:
            content = self._download(url, is_video=is_video, deadline=deadline)
            content = prepare_video(content, deadline=deadline) if is_video else _require_content(content)
        except MediaDownloadError as error:
            logger.warning("Media download failed (%s)", error.code)
            raise
        logger.info("Worker downloaded %s media bytes", len(content))
        return content

    def _download(self, url: str, *, is_video: bool, deadline: ExtractionDeadline | None = None) -> bytes:
        clean_url = html.unescape(url)
        if is_video and (".m3u8" in clean_url or "blob:" in clean_url):
            return download_ytdlp_bytes(clean_url, destination_policy=self._destination_policy, deadline=deadline)
        try:
            content = self._direct_download(clean_url)
        except MediaDownloadError:
            if not is_video:
                raise
            return download_ytdlp_bytes(clean_url, destination_policy=self._destination_policy, deadline=deadline)
        if is_video and is_hls_manifest(content):
            return download_ytdlp_bytes(clean_url, destination_policy=self._destination_policy, deadline=deadline)
        return content

    def _download_item(self, item: MediaItem, *, deadline: ExtractionDeadline | None = None) -> bytes | MediaFailure:
        try:
            if item.data is not None:
                return prepare_video(item.data, deadline=deadline) if item.is_video else _require_content(item.data)
            if item.requires_download:
                content = download_ytdlp_bytes(item.url, destination_policy=self._destination_policy, deadline=deadline)
                return prepare_video(content, deadline=deadline) if item.is_video else _require_content(content)
            return self.download(item.url, is_video=item.is_video, deadline=deadline)
        except MediaDownloadError as error:
            return error.code

    def download_batch(
        self, items: list[MediaItem], *, deadline: ExtractionDeadline | None = None
    ) -> list[bytes | MediaFailure]:
        """Download every item while preserving order and explicit failures.

        Existing in-memory bytes are reused without a second download.

        Args:
            items: Media items to download in order.
            deadline: Optional shared deadline bounding batch downloads.

        Returns:
            One bytes payload or MediaFailure per input item, in input order.
        """
        allowance = deadline or ExtractionDeadline.after(BATCH_TIMEOUT_SECONDS)
        return [self._download_item(item, deadline=allowance) for item in items]
