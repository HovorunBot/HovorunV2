"""Required bytes and explicit partial batch results using real HTTP and yt-dlp."""

import asyncio
import tempfile
from http import HTTPStatus
from pathlib import Path

import pytest

from hovorunv2.application.media.downloader import (
    NativeMediaDownloader,
    download_ytdlp_bytes,
)
from hovorunv2.application.media.errors import MediaDownloadError, MediaFailure
from hovorunv2.application.media.payloads import MediaItem
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from tests.utils.sync_http import HTTPFixture, HTTPReply


async def test_native_batches_retain_order_with_explicit_partial_failures() -> None:
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
        native = NativeMediaDownloader(http)
        items = [MediaItem(server.url(path), is_video=False) for path in ("/good", "/missing", "/empty")]
        direct = await asyncio.to_thread(native.download_batch, items)
        assert direct == [b"synthetic media", MediaFailure.UNAVAILABLE, MediaFailure.UNUSABLE]
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
