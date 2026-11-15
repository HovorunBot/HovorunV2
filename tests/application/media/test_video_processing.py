"""Exercise real FFmpeg size reduction, audio preservation, and bounded failures."""

from pathlib import Path
from typing import Any

import pytest

from hovorunv2.application.media import video_processing
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaDownloadError, MediaFailure
from hovorunv2.application.media.video_processing import prepare_video
from tests.utils.split_media import media_stream_types, synthetic_video

pytestmark = pytest.mark.unit
OUTPUT_BYTES = 100_000


@pytest.mark.parametrize("with_audio", [True, False])
def test_video_compression_preserves_streams_and_operates_on_disk(tmp_path: Path, *, with_audio: bool) -> None:
    """Produce an actual bounded MP4 on disk without losing audio or loading full bytes."""
    source = tmp_path / "source.mp4"
    source.write_bytes(synthetic_video(tmp_path, with_audio=with_audio) + bytes(OUTPUT_BYTES))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    compressed = prepare_video(source, work_dir=work_dir, max_bytes=OUTPUT_BYTES)
    assert isinstance(compressed, Path)
    assert compressed.is_file()
    assert compressed.is_relative_to(work_dir)
    assert 0 < compressed.stat().st_size <= OUTPUT_BYTES < source.stat().st_size
    assert media_stream_types(compressed) == ({"video", "audio"} if with_audio else {"video"})


def test_small_video_is_unchanged_without_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary videos avoid transcoding and do not require processing tools."""
    source = tmp_path / "small.mp4"
    source.write_bytes(b"original bytes")
    monkeypatch.setenv("PATH", "")
    assert prepare_video(source) == source


@pytest.mark.parametrize("guard", ["duration", "quality", "input", "dimensions", "deadline", "executable"])
def test_video_processing_guards_fail_explicitly_and_clean_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    """Resource and quality limits must reject rather than return truncated or silent output."""
    source = tmp_path / "source.mp4"
    source.write_bytes(synthetic_video(tmp_path) + bytes(OUTPUT_BYTES))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
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
        prepare_video(source, work_dir=work_dir, max_bytes=limit, deadline=deadline)
    assert error.value.code == expected


def test_unusable_video_cannot_be_reported_as_success(tmp_path: Path) -> None:
    """Invalid downloaded files never become an apparent compressed video."""
    source = tmp_path / "invalid.mp4"
    source.write_bytes(b"invalid media" * 100)
    with pytest.raises(MediaDownloadError):
        prepare_video(source, max_bytes=100)


def test_video_processing_wraps_subprocesses_with_prlimit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FFprobe and FFmpeg invocations are wrapped in prlimit with --as and --cpu bounds."""
    source = tmp_path / "source.mp4"
    source.write_bytes(synthetic_video(tmp_path) + bytes(OUTPUT_BYTES))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    recorded: list[list[str]] = []
    real_run: Any = video_processing.subprocess.run

    def spy_run(args: list[str], *pos: object, **kwargs: object) -> object:
        recorded.append(list(args))
        return real_run(args, *pos, **kwargs)

    monkeypatch.setattr(video_processing.subprocess, "run", spy_run)
    prepare_video(source, work_dir=work_dir, max_bytes=OUTPUT_BYTES)
    assert recorded
    for cmd in recorded:
        assert Path(cmd[0]).name == "prlimit"
        assert any(arg.startswith("--as=") for arg in cmd)
        assert any(arg.startswith("--cpu=") for arg in cmd)
