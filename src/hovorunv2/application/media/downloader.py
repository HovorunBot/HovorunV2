"""Application service for native synchronous media downloading."""

import html
import logging
import time
from collections.abc import Callable, Mapping
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


def is_hls_manifest_file(path: Path) -> bool:
    """Return whether the file on disk is a small HLS manifest."""
    if not path.is_file() or path.stat().st_size >= HLS_MANIFEST_SIZE_THRESHOLD:
        return False
    with path.open("rb") as stream:
        head = stream.read(HLS_MANIFEST_SIZE_THRESHOLD)
    return "#EXTM3U" in head.decode("utf-8", errors="ignore")


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


def _require_file(output: Path, max_bytes: int = MAX_MEDIA_BYTES, *, root: Path | None = None) -> Path:
    if root is not None and not output.resolve().is_relative_to(root.resolve()):
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    if not output.is_file():
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    size = output.stat().st_size
    if size > max_bytes:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    if size == 0:
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    return output


def _download_options(
    root: Path,
    max_bytes: int,
    *,
    timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
    prefix: str = "downloaded",
) -> dict[str, object]:
    bounded_timeout = max(1, int(timeout_seconds))
    return {
        "format": '(bestvideo+bestaudio/best)[protocol~="^(https?|m3u8)"]',
        "merge_output_format": "mp4",
        "outtmpl": str(root / f"{prefix}.%(ext)s"),
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
    url: str,
    root: Path,
    max_bytes: int,
    *,
    timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
    prefix: str = "downloaded",
) -> Path:
    try:
        with yt_dlp.YoutubeDL(
            _download_options(root, max_bytes, timeout_seconds=timeout_seconds, prefix=prefix)
        ) as ydl:
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


def download_ytdlp_file(
    url: str,
    dest_dir: Path,
    *,
    destination_policy: DestinationPolicyPort,
    max_bytes: int = MAX_SOURCE_MEDIA_BYTES,
    deadline: ExtractionDeadline | None = None,
    prefix: str = "downloaded",
) -> Path:
    """Download bounded media to a file inside ``dest_dir`` using yt-dlp.

    Args:
        url: Media URL to download.
        dest_dir: Caller-owned directory where the downloaded file will be stored.
        destination_policy: Mandatory policy validating public network destinations.
        max_bytes: Upper bound on the downloaded payload.
        deadline: Optional extraction deadline bounding execution wall-clock time.
        prefix: Filename prefix inside ``dest_dir``.

    Returns:
        Path to the downloaded media file inside ``dest_dir``.

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
        with guard:
            output = _download_to_file(url, dest_dir, max_bytes, timeout_seconds=remaining, prefix=prefix)
            return _require_file(output, max_bytes, root=dest_dir)
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

    def _direct_download_to_path(self, url: str, dest: Path) -> Path:
        try:
            self._http.download_to_path(url, dest, headers=DEFAULT_DOWNLOAD_HEADERS)
        except HTTPTransportError as error:
            code = MediaFailure.TOO_LARGE if error.code == HTTPFailure.TOO_LARGE else MediaFailure.UNAVAILABLE
            raise MediaDownloadError(code) from None
        return _require_file(dest, MAX_SOURCE_MEDIA_BYTES)

    def download_to_path(
        self,
        url: str,
        dest_dir: Path,
        *,
        is_video: bool = False,
        deadline: ExtractionDeadline | None = None,
        on_compression: Callable[[], None] | None = None,
        index: int = 0,
    ) -> Path:
        """Download media to a bounded file in ``dest_dir`` or fall back to yt-dlp.

        Args:
            url: Media URL to download.
            dest_dir: Caller-owned temporary directory.
            is_video: Whether the media is a video.
            deadline: Optional shared deadline bounding the download.
            on_compression: Optional callback invoked immediately before compression starts.
            index: Item index used to keep filenames distinct inside ``dest_dir``.

        Returns:
            Path to the downloaded (and possibly compressed) media file.

        Raises:
            MediaDownloadError: If the download fails and no fallback applies.
        """
        logger.info("Worker starting media download")
        try:
            path = self._download_to_path(url, dest_dir, is_video=is_video, deadline=deadline, index=index)
            path = (
                prepare_video(path, work_dir=dest_dir, deadline=deadline, on_compression=on_compression)
                if is_video
                else _require_file(path)
            )
        except MediaDownloadError as error:
            logger.warning("Media download failed (%s)", error.code)
            raise
        logger.info("Worker downloaded %s media bytes", path.stat().st_size)
        return path

    def _download_to_path(
        self,
        url: str,
        dest_dir: Path,
        *,
        is_video: bool,
        deadline: ExtractionDeadline | None = None,
        index: int = 0,
    ) -> Path:
        clean_url = html.unescape(url)
        prefix = f"ytdlp_{index}"
        if is_video and (".m3u8" in clean_url or "blob:" in clean_url):
            return download_ytdlp_file(
                clean_url, dest_dir, destination_policy=self._destination_policy, deadline=deadline, prefix=prefix
            )
        dest = dest_dir / f"source_{index}.media"
        try:
            path = self._direct_download_to_path(clean_url, dest)
        except MediaDownloadError:
            if not is_video:
                raise
            return download_ytdlp_file(
                clean_url, dest_dir, destination_policy=self._destination_policy, deadline=deadline, prefix=prefix
            )
        if is_video and is_hls_manifest_file(path):
            return download_ytdlp_file(
                clean_url, dest_dir, destination_policy=self._destination_policy, deadline=deadline, prefix=prefix
            )
        return path

    def _download_item_to_path(
        self,
        item: MediaItem,
        dest_dir: Path,
        index: int,
        *,
        deadline: ExtractionDeadline | None = None,
        on_compression: Callable[[], None] | None = None,
    ) -> Path | MediaFailure:
        try:
            if item.file_path is not None:
                return (
                    prepare_video(item.file_path, work_dir=dest_dir, deadline=deadline, on_compression=on_compression)
                    if item.is_video
                    else _require_file(item.file_path)
                )
            if item.data is not None:
                source = dest_dir / f"item_{index}.media"
                source.write_bytes(item.data)
                return (
                    prepare_video(source, work_dir=dest_dir, deadline=deadline, on_compression=on_compression)
                    if item.is_video
                    else _require_file(source)
                )
            if item.requires_download:
                path = download_ytdlp_file(
                    item.url,
                    dest_dir,
                    destination_policy=self._destination_policy,
                    deadline=deadline,
                    prefix=f"ytdlp_{index}",
                )
                return (
                    prepare_video(path, work_dir=dest_dir, deadline=deadline, on_compression=on_compression)
                    if item.is_video
                    else _require_file(path)
                )
            return self.download_to_path(
                item.url,
                dest_dir,
                is_video=item.is_video,
                deadline=deadline,
                on_compression=on_compression,
                index=index,
            )
        except MediaDownloadError as error:
            return error.code

    def download_batch(
        self,
        items: list[MediaItem],
        dest_dir: Path,
        *,
        deadline: ExtractionDeadline | None = None,
        on_compression: Callable[[], None] | None = None,
    ) -> list[Path | MediaFailure]:
        """Download every item to ``dest_dir`` while preserving order and explicit failures.

        Existing file paths or in-memory bytes are reused without a second download.

        Args:
            items: Media items to download in order.
            dest_dir: Caller-owned temporary directory for downloaded/compressed files.
            deadline: Optional shared deadline bounding batch downloads.
            on_compression: Optional callback invoked immediately before compression starts.

        Returns:
            One file Path or MediaFailure per input item, in input order.
        """
        allowance = deadline or ExtractionDeadline.after(BATCH_TIMEOUT_SECONDS)
        return [
            self._download_item_to_path(item, dest_dir, index, deadline=allowance, on_compression=on_compression)
            for index, item in enumerate(items)
        ]
