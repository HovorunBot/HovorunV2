"""Generate synthetic split audio/video streams and inspect real merged output."""

import json
import shutil
import subprocess
from pathlib import Path

from tests.utils.sync_http import HTTPReply


def split_media_routes(root: Path) -> dict[str, HTTPReply]:
    """Create a short DASH presentation with independently served audio and video."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        msg = "FFmpeg is required for split-stream regression coverage."
        raise FileNotFoundError(msg)
    subprocess.run(  # noqa: S603
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "0.5",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-use_template",
            "0",
            "-use_timeline",
            "0",
            "-f",
            "dash",
            str(root / "manifest.mpd"),
        ],
        check=True,
        capture_output=True,
    )
    return {
        f"/{path.name}": HTTPReply(
            path.read_bytes(),
            headers={"Content-Type": "application/dash+xml" if path.suffix == ".mpd" else "video/mp4"},
        )
        for path in root.iterdir()
    }


def media_stream_types(path: Path) -> set[str]:
    """Inspect the codecs in an actual media file rather than trusting metadata."""
    executable = shutil.which("ffprobe")
    if executable is None:
        msg = "FFprobe is required for split-stream regression coverage."
        raise FileNotFoundError(msg)
    result = subprocess.run(  # noqa: S603
        [executable, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return {stream["codec_type"] for stream in json.loads(result.stdout)["streams"]}


def synthetic_video(root: Path, *, with_audio: bool = True) -> bytes:
    """Create a complete short video with audible synthetic audio."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        msg = "FFmpeg is required for video regression coverage."
        raise FileNotFoundError(msg)
    output = root / "source.mp4"
    subprocess.run(  # noqa: S603
        [
            executable,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-crf",
            "10",
            "-c:a",
            "aac",
            *([] if with_audio else ["-an"]),
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output.read_bytes()
