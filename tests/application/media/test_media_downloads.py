"""Required bytes and explicit partial batch results using real HTTP and yt-dlp."""

import asyncio
import time
from http import HTTPStatus
from pathlib import Path

import pytest
import yt_dlp

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.downloader import (
    NativeMediaDownloader,
    _enforce_download_limit,
    download_ytdlp_file,
)
from hovorunv2.application.media.errors import MediaDownloadError, MediaFailure
from hovorunv2.application.media.payloads import MediaItem
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from tests.utils.sync_http import HTTPFixture, HTTPReply


async def test_native_batches_retain_order_with_explicit_partial_failures(tmp_path: Path) -> None:
    """Only the batch policy translates semantic failures; standalone downloads require usable files."""
    with (
        HTTPFixture(
            {
                "/good": HTTPReply(b"synthetic media"),
                "/missing": HTTPReply(status=HTTPStatus.NOT_FOUND),
                "/empty": HTTPReply(),
            }
        ) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as http,
    ):
        native = NativeMediaDownloader(http, destination_policy=http)
        items = [MediaItem(server.url(path), is_video=False) for path in ("/good", "/missing", "/empty")]
        direct = await asyncio.to_thread(native.download_batch, items, tmp_path)
        assert isinstance(direct[0], Path)
        assert direct[0].read_bytes() == b"synthetic media"
        assert direct[1:] == [MediaFailure.UNAVAILABLE, MediaFailure.UNUSABLE]
        with pytest.raises(MediaDownloadError):
            await asyncio.to_thread(native.download_to_path, server.url("/missing"), tmp_path)


def test_ytdlp_output_is_bounded_and_written_to_caller_directory(tmp_path: Path) -> None:
    """Real backend downloads write bounded files into caller-owned dir and reject oversized output."""
    with (
        HTTPFixture({"/asset.mp4": HTTPReply(b"synthetic media", headers={"Content-Type": "video/mp4"})}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as http,
    ):
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        downloaded = download_ytdlp_file(server.url("/asset.mp4"), ok_dir, destination_policy=http)
        assert downloaded.is_relative_to(ok_dir)
        assert downloaded.read_bytes() == b"synthetic media"
        oversized_dir = tmp_path / "oversized"
        oversized_dir.mkdir()
        with pytest.raises(MediaDownloadError):
            download_ytdlp_file(server.url("/asset.mp4"), oversized_dir, destination_policy=http, max_bytes=4)


def test_ytdlp_fallback_rejects_file_urls_without_backend_invocation(tmp_path: Path) -> None:
    """File scheme URLs are rejected before invoking yt-dlp."""
    with NativeHTTPClient() as http:
        native = NativeMediaDownloader(http, destination_policy=http)
        with pytest.raises(MediaDownloadError) as error:
            native.download_to_path("file:///etc/hostname", tmp_path, is_video=True)
        assert error.value.code == MediaFailure.UNAVAILABLE


def test_ytdlp_fallback_rejects_loopback_destinations_without_backend_invocation(tmp_path: Path) -> None:
    """Loopback destinations without explicit test allowlist never receive requests."""
    with (
        HTTPFixture({"/x.m3u8": HTTPReply(b"#EXTM3U")}) as server,
        NativeHTTPClient() as http,
    ):
        native = NativeMediaDownloader(http, destination_policy=http)
        with pytest.raises(MediaDownloadError) as error:
            native.download_to_path(server.url("/x.m3u8"), tmp_path, is_video=True)
        assert error.value.code == MediaFailure.UNAVAILABLE
        assert server.requests == []


def test_ytdlp_fallback_bounds_total_wall_clock_time_with_deadline(tmp_path: Path) -> None:
    """An expired or depleted deadline raises MediaDownloadError without network I/O."""
    with (
        HTTPFixture({"/asset.mp4": HTTPReply(b"synthetic media")}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as http,
    ):
        expired_deadline = ExtractionDeadline(time.monotonic() - 10.0)
        with pytest.raises(MediaDownloadError) as error:
            download_ytdlp_file(server.url("/asset.mp4"), tmp_path, destination_policy=http, deadline=expired_deadline)
        assert error.value.code == MediaFailure.UNAVAILABLE
        assert server.requests == []


def test_enforce_download_limit_fragment_and_total_byte_bounds() -> None:
    """Progress hook enforces bounds across downloaded, total, and estimated fragment bytes."""
    _enforce_download_limit({"downloaded_bytes": 100}, max_bytes=100)
    _enforce_download_limit({"total_bytes": 100}, max_bytes=100)
    _enforce_download_limit({"total_bytes_estimate": 100.0}, max_bytes=100)

    with pytest.raises(yt_dlp.utils.DownloadError):
        _enforce_download_limit({"downloaded_bytes": 101}, max_bytes=100)
    with pytest.raises(yt_dlp.utils.DownloadError):
        _enforce_download_limit({"total_bytes": 101}, max_bytes=100)
    with pytest.raises(yt_dlp.utils.DownloadError):
        _enforce_download_limit({"total_bytes_estimate": 101.5}, max_bytes=100)
