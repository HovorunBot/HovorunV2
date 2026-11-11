"""Compress oversized provider videos with bounded local FFmpeg operations."""

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaDownloadError, MediaExtractionError, MediaFailure
from hovorunv2.application.media.limits import MAX_SOURCE_MEDIA_BYTES, TELEGRAM_VIDEO_BYTES

COMPRESSION_SECONDS = 45
PROBE_SECONDS = 5
MAX_DURATION_SECONDS = 600
MAX_INPUT_PIXELS = 4096 * 2160
MIN_VIDEO_BITRATE = 200_000
MAX_VIDEO_BITRATE = 4_000_000
AUDIO_BITRATE = 64_000
BITS_PER_BYTE = 8
OUTPUT_BUDGET_RATIO = 0.90
DURATION_TOLERANCE_SECONDS = 0.25
LOCAL_FORMATS = "mov,matroska,webm,avi,mpeg,mpegvideo,mpegts,flv,ogg,asf"


@dataclass(frozen=True)
class _VideoFacts:
    duration: float
    has_audio: bool


def _executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise MediaDownloadError(MediaFailure.UNAVAILABLE)
    return executable


def _run(arguments: list[str], deadline: ExtractionDeadline, *, capture: bool = False) -> bytes:
    result = subprocess.run(  # noqa: S603
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=deadline.remaining(),
        check=True,
    )
    return result.stdout if capture else b""


def _probe(path: Path, deadline: ExtractionDeadline) -> _VideoFacts:
    raw = _run(
        [
            _executable("ffprobe"),
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            LOCAL_FORMATS,
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        deadline.narrow(PROBE_SECONDS),
        capture=True,
    )
    facts = json.loads(raw)
    duration = float(facts["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    if duration > MAX_DURATION_SECONDS:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    streams = facts["streams"]
    videos = [stream for stream in streams if stream["codec_type"] == "video"]
    if not videos:
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    if int(videos[0]["width"]) * int(videos[0]["height"]) > MAX_INPUT_PIXELS:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    return _VideoFacts(duration, any(stream["codec_type"] == "audio" for stream in streams))


def _encode(source: Path, output: Path, bitrate: int, max_bytes: int, deadline: ExtractionDeadline) -> None:
    common = [
        _executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-threads",
        "2",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        LOCAL_FORMATS,
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-vf",
        "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
        "-filter_threads",
        "1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-threads",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        str(bitrate),
        "-passlogfile",
        str(source.parent / "pass"),
    ]
    _run([*common, "-pass", "1", "-an", "-f", "null", "-"], deadline)
    _run(
        [
            *common,
            "-pass",
            "2",
            "-map",
            "0:a:0?",
            "-c:a",
            "aac",
            "-b:a",
            str(AUDIO_BITRATE),
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-fs",
            str(max_bytes + 1),
            str(output),
        ],
        deadline,
    )


def _compress(content: bytes, max_bytes: int, deadline: ExtractionDeadline) -> bytes:
    with tempfile.TemporaryDirectory(prefix="hovorun-video-") as directory:
        source = Path(directory) / "source.media"
        output = Path(directory) / "compressed.mp4"
        source.write_bytes(content)
        facts = _probe(source, deadline)
        total_bitrate = int(max_bytes * BITS_PER_BYTE * OUTPUT_BUDGET_RATIO / facts.duration)
        video_bitrate = total_bitrate - (AUDIO_BITRATE if facts.has_audio else 0)
        if video_bitrate < MIN_VIDEO_BITRATE:
            raise MediaDownloadError(MediaFailure.TOO_LARGE)
        _encode(source, output, min(video_bitrate, MAX_VIDEO_BITRATE), max_bytes, deadline)
        if output.stat().st_size > max_bytes:
            raise MediaDownloadError(MediaFailure.TOO_LARGE)
        result = _probe(output, deadline)
        if facts.duration - result.duration > DURATION_TOLERANCE_SECONDS:
            raise MediaDownloadError(MediaFailure.UNUSABLE)
        if facts.has_audio and not result.has_audio:
            raise MediaDownloadError(MediaFailure.UNUSABLE)
        return output.read_bytes()


def prepare_video(
    content: bytes, *, max_bytes: int = TELEGRAM_VIDEO_BYTES, deadline: ExtractionDeadline | None = None
) -> bytes:
    """Keep small videos intact or require a complete, bounded H.264/AAC replacement.

    Args:
        content: Downloaded video bytes, never a remote URL or playlist.
        max_bytes: Maximum permitted output size, including container overhead.
        deadline: Optional shared cutoff for the owning download batch.

    Returns:
        Original bytes or a compressed MP4 preserving video duration and audio.

    Raises:
        ValueError: If the requested output bound is not positive.
        MediaDownloadError: If input, quality, duration, size or execution bounds fail.
    """
    if max_bytes <= 0:
        msg = "Video output bound must be positive."
        raise ValueError(msg)
    if not content:
        raise MediaDownloadError(MediaFailure.UNUSABLE)
    if len(content) > MAX_SOURCE_MEDIA_BYTES:
        raise MediaDownloadError(MediaFailure.TOO_LARGE)
    if len(content) <= max_bytes:
        return content
    try:
        allowance = (deadline or ExtractionDeadline.after(COMPRESSION_SECONDS)).narrow(COMPRESSION_SECONDS)
        return _compress(content, max_bytes, allowance)
    except MediaDownloadError:
        raise
    except OSError, subprocess.SubprocessError, MediaExtractionError:
        raise MediaDownloadError(MediaFailure.UNAVAILABLE) from None
    except ValueError, KeyError, TypeError:
        raise MediaDownloadError(MediaFailure.UNUSABLE) from None
