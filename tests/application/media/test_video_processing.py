"""Exercise real FFmpeg size reduction, audio preservation, and bounded failures."""

import tempfile
from pathlib import Path

import pytest

from hovorunv2.application.media import video_processing
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaDownloadError, MediaFailure
from hovorunv2.application.media.video_processing import prepare_video
from tests.utils.split_media import media_stream_types, synthetic_video

pytestmark = pytest.mark.unit
OUTPUT_BYTES = 100_000


@pytest.mark.parametrize("with_audio", [True, False])
def test_video_compression_preserves_streams_and_cleans_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_audio: bool
) -> None:
    """Produce an actual bounded MP4 without losing audio or retaining processing files."""
    content = synthetic_video(tmp_path, with_audio=with_audio) + bytes(OUTPUT_BYTES)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    compressed = prepare_video(content, max_bytes=OUTPUT_BYTES)
    assert 0 < len(compressed) <= OUTPUT_BYTES < len(content)
    assert set(tmp_path.iterdir()) == {tmp_path / "source.mp4"}
    output = tmp_path / "compressed.mp4"
    output.write_bytes(compressed)
    assert media_stream_types(output) == ({"video", "audio"} if with_audio else {"video"})


def test_small_video_is_unchanged_without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary videos avoid transcoding and do not require processing tools."""
    monkeypatch.setenv("PATH", "")
    assert prepare_video(b"original bytes") == b"original bytes"


@pytest.mark.parametrize("guard", ["duration", "quality", "input", "dimensions", "deadline", "executable"])
def test_video_processing_guards_fail_explicitly_and_clean_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    """Resource and quality limits must reject rather than return truncated or silent output."""
    content = synthetic_video(tmp_path) + bytes(OUTPUT_BYTES)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limit = OUTPUT_BYTES
    deadline = None
    expected = MediaFailure.TOO_LARGE
    if guard == "duration":
        monkeypatch.setattr(video_processing, "MAX_DURATION_SECONDS", 1)
    if guard == "quality":
        limit = 1000
    if guard == "input":
        monkeypatch.setattr(video_processing, "MAX_SOURCE_MEDIA_BYTES", 32)
    if guard == "dimensions":
        monkeypatch.setattr(video_processing, "MAX_INPUT_PIXELS", 32)
    if guard == "deadline":
        deadline = ExtractionDeadline.after(0.000001)
        expected = MediaFailure.UNAVAILABLE
    if guard == "executable":
        monkeypatch.setenv("PATH", "")
        expected = MediaFailure.UNAVAILABLE
    with pytest.raises(MediaDownloadError) as error:
        prepare_video(content, max_bytes=limit, deadline=deadline)
    assert error.value.code == expected
    assert set(tmp_path.iterdir()) == {tmp_path / "source.mp4"}


def test_unusable_video_cannot_be_reported_as_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid downloaded bytes never become an apparent compressed video."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(MediaDownloadError):
        prepare_video(b"invalid media" * 100, max_bytes=100)
    assert not list(tmp_path.iterdir())
