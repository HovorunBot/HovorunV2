"""Backend request bounds leave time for worker fallback and cleanup."""

import instaloader
import pytest
import yt_dlp

from hovorunv2.application.media.backend_logging import MediaBackendFailure, MediaBackendLogger
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import EXTRACTION_TIMEOUT_SECONDS, BoundedYoutubeDL
from hovorunv2.application.media.providers.instagram import FailFastRateController, PrivateInstaloaderContext
from hovorunv2.infrastructure.logger import PrivacyFormatter
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = pytest.mark.unit
SLOW_REQUEST_SECONDS = 0.6
EXTRACTION_BUDGET_SECONDS = 0.3


def test_instagram_bounds_requests_and_never_sleeps_through_rate_limits() -> None:
    """Use one 30-second attempt and fail over immediately when a rate-limit wait is required."""
    context = PrivateInstaloaderContext()
    try:
        assert context.request_timeout == EXTRACTION_TIMEOUT_SECONDS
        assert context.max_connection_attempts == 1
        assert not context.sleep
        with pytest.raises(instaloader.exceptions.ConnectionException, match="rate-limit wait"):
            FailFastRateController(context).sleep(120)
    finally:
        context.close()


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
