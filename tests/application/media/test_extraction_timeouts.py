"""Backend request bounds leave time for worker fallback and cleanup."""

import time

import pytest
import yt_dlp

from hovorunv2.application.media.backend_logging import MediaBackendFailure, MediaBackendLogger
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import BoundedYoutubeDL, extract_info
from hovorunv2.infrastructure.logger import PrivacyFormatter
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = pytest.mark.unit
SLOW_REQUEST_SECONDS = 0.6
EXTRACTION_BUDGET_SECONDS = 0.3
TIMING_TOLERANCE_SECONDS = 0.7


@pytest.mark.parametrize(
    "reply",
    [
        HTTPReply(b"<html>late</html>", delay_seconds=0.8),
        HTTPReply(chunks=(b"<html>", b"one", b"two", b"three", b"</html>"), chunk_delay_seconds=0.2),
    ],
)
def test_native_ytdlp_total_deadline_bounds_slow_headers_and_drip_body(reply: HTTPReply) -> None:
    """Bound the complete native extraction call even while response bytes keep arriving."""
    with BoundedYoutubeDL({"logger": MediaBackendLogger()}) as backend:
        backend.get_info_extractor("Generic")
    with HTTPFixture({"/slow": reply}) as server:
        started = time.monotonic()
        with pytest.raises(MediaExtractionError):
            extract_info(server.url("/slow"), deadline=ExtractionDeadline.after(EXTRACTION_BUDGET_SECONDS))
        assert time.monotonic() - started < EXTRACTION_BUDGET_SECONDS + TIMING_TOLERANCE_SECONDS
        assert len(server.requests) == 1


def test_ytdlp_request_budget_stops_slow_request_and_prevents_another() -> None:
    """A real slow external response consumes the shared budget instead of restarting it per request."""
    with HTTPFixture({"/slow": HTTPReply(disconnect=True, delay_seconds=SLOW_REQUEST_SECONDS)}) as server:
        with BoundedYoutubeDL({"logger": MediaBackendLogger()}, EXTRACTION_BUDGET_SECONDS) as backend:
            with pytest.raises(yt_dlp.networking.exceptions.RequestError):
                backend.urlopen(server.url("/slow"))
            with pytest.raises(MediaExtractionError, match="request budget expired"):
                backend.urlopen(server.url("/slow"))
        assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("diagnostic", "reason"),
    [
        ("HTTP Error 429: Too Many Requests", MediaBackendFailure.RATE_LIMITED),
        ("Sign in to confirm", MediaBackendFailure.AUTHENTICATION),
        ("Request timed out", MediaBackendFailure.TIMEOUT),
        ("HTTP Error 403", MediaBackendFailure.FORBIDDEN),
        ("HTTP Error 404", MediaBackendFailure.NOT_FOUND),
        ("Unsupported URL", MediaBackendFailure.UNSUPPORTED),
    ],
)
def test_backend_errors_report_only_allowlisted_reasons(
    diagnostic: str, reason: MediaBackendFailure, caplog: pytest.LogCaptureFixture
) -> None:
    """Owner diagnostics keep an actionable category without including backend-supplied values."""
    private_input = "synthetic-private-backend-input"
    MediaBackendLogger().error(diagnostic + " " + private_input)
    record = caplog.records[-1]
    formatted = PrivacyFormatter("%(message)s").format(record)
    assert reason in formatted
    assert private_input not in formatted
