"""Required bytes and explicit partial batch results using real HTTP and yt-dlp."""

import asyncio
import tempfile
from http import HTTPStatus
from pathlib import Path

import aiohttp
import pytest
from aiogram.types import BufferedInputFile

from hovorunv2.application.media.downloader import (
    MAX_MEDIA_BYTES,
    MediaDownloader,
    NativeMediaDownloader,
    download_ytdlp_bytes,
)
from hovorunv2.application.media.errors import MediaDownloadError, MediaFailure
from hovorunv2.application.media.payloads import MediaItem
from hovorunv2.infrastructure.native_http import NativeHTTPClient
from tests.utils.sync_http import HTTPFixture, HTTPReply


async def test_native_and_async_batches_retain_order_with_explicit_partial_failures() -> None:
    """Only the batch policy translates semantic failures; standalone downloads require usable bytes."""
    with (
        HTTPFixture(
            {
                "/good": HTTPReply(b"synthetic media"),
                "/missing": HTTPReply(status=HTTPStatus.NOT_FOUND),
                "/empty": HTTPReply(),
            }
        ) as server,
        NativeHTTPClient() as http,
    ):
        async with aiohttp.ClientSession() as session:
            native = NativeMediaDownloader(http)
            asynchronous = MediaDownloader(session)
            items = [MediaItem(server.url(path), is_video=False) for path in ("/good", "/missing", "/empty")]
            downloaded = await asynchronous.download_batch(items)
            assert isinstance(downloaded[0], BufferedInputFile)
            assert downloaded[0].data == b"synthetic media"
            assert downloaded[1:] == [MediaFailure.UNAVAILABLE, MediaFailure.UNUSABLE]
            direct = await asyncio.to_thread(native.download_batch, items)
            assert direct == [b"synthetic media", MediaFailure.UNAVAILABLE, MediaFailure.UNUSABLE]
            with pytest.raises(MediaDownloadError):
                await asynchronous.download_as_buffered_file(server.url("/missing"), "synthetic.jpg")
            with pytest.raises(MediaDownloadError):
                await asyncio.to_thread(native.download, server.url("/missing"))


def test_ytdlp_output_is_bounded_and_temporary_artifacts_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real backend downloads clean their owned directory on success and rejected oversized output."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with HTTPFixture({"/asset.mp4": HTTPReply(b"synthetic media", headers={"Content-Type": "video/mp4"})}) as server:
        assert download_ytdlp_bytes(server.url("/asset.mp4")) == b"synthetic media"
        assert list(tmp_path.iterdir()) == []
        with pytest.raises(MediaDownloadError):
            download_ytdlp_bytes(server.url("/asset.mp4"), max_bytes=4)
        assert list(tmp_path.iterdir()) == []


async def test_direct_oversize_does_not_retry_the_video_backend() -> None:
    """An oversized declared response fails before reading and retains the original no-fallback size policy."""
    with HTTPFixture({"/large": HTTPReply(b"x", headers={"Content-Length": str(MAX_MEDIA_BYTES + 1)})}) as server:
        async with aiohttp.ClientSession() as session:
            downloader = MediaDownloader(session)
            with pytest.raises(MediaDownloadError) as error:
                await downloader.download_as_buffered_file(server.url("/large"), "synthetic.mp4", is_video=True)
            assert error.value.code == MediaFailure.TOO_LARGE
            assert [request.method for request in server.requests] == ["GET"]
