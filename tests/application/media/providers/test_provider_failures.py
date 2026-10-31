"""Required provider I/O and final strategy failure using synchronous worker clients and fixtures."""

import pytest

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.providers.base import BaseMediaClient
from hovorunv2.application.media.providers.bluesky import BlueskyService
from hovorunv2.application.media.providers.tiktok import TikTokService
from hovorunv2.application.media.providers.twitter import TwitterService
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.unit
@pytest.mark.parametrize(
    ("service", "url", "endpoint"),
    [
        (TwitterService.sync(), "https://x.com/synthetic/status/123", "api.vxtwitter.com"),
        (TikTokService.sync(), "https://www.tiktok.com/@synthetic/video/123", "www.tikwm.com/api/"),
        (BlueskyService.sync(), "https://bsky.app/profile/synthetic.test/post/abc", "public.api.bsky.app"),
    ],
)
@pytest.mark.parametrize("payload", [None, [], {"unexpected": "synthetic"}])
def test_sync_unusable_api_data_raises_media_extraction_error(
    service: BaseMediaClient,
    url: str,
    endpoint: str,
    payload: object,
) -> None:
    """Synchronous worker extraction rejects unusable/malformed API payloads as semantic failures."""
    http = FakeSyncHTTP(json_responses={endpoint: payload})
    assert service.extract_sync("https://synthetic.invalid/unsupported", http) is None
    with pytest.raises(MediaExtractionError):
        service.extract_sync(url, http)


@pytest.mark.unit
def test_malformed_gallery_cannot_be_reinterpreted_as_character_media() -> None:
    """A non-array gallery is rejected before DTO construction instead of producing one URL per character."""
    service = TikTokService.sync()
    http = FakeSyncHTTP(json_responses={"www.tikwm.com/api/": {"code": 0, "data": {"images": "invalid"}}})
    with pytest.raises(MediaExtractionError, match="gallery metadata"):
        service.extract_sync("https://www.tiktok.com/@synthetic/video/123", http)


@pytest.mark.unit
def test_minimal_valid_provider_response_remains_usable() -> None:
    """Positive control proves the external JSON fixture reaches the actual provider parser and defaults."""
    service = TwitterService.sync()
    source = "https://x.com/synthetic/status/123"
    responses = {"api.vxtwitter.com/i/status/123": {"text": "synthetic post", "media_extended": []}}
    http = FakeSyncHTTP(json_responses=responses)
    payload = service.extract_sync(source, http)
    assert payload is not None
    assert payload.content == "synthetic post"
    assert payload.author_name == "Unknown"
    assert payload.original_url == source


@pytest.mark.unit
@pytest.mark.parametrize("text", [None, 123, []])
def test_invalid_required_provider_fields_fail_before_rendering(text: object) -> None:
    """Wrong typed fields never become a nominal payload that fails later during escaping or delivery."""
    service = TwitterService.sync()
    http = FakeSyncHTTP(json_responses={"api.vxtwitter.com/i/status/123": {"text": text}})
    with pytest.raises(MediaExtractionError, match="unusable extraction metadata"):
        service.extract_sync("https://x.com/synthetic/status/123", http)


@pytest.mark.unit
def test_selected_video_provider_requires_media_in_its_success_response() -> None:
    """A successful API code without any rendition is a failed extraction, not a placeholder-only post."""
    service = TikTokService.sync()
    http = FakeSyncHTTP(json_responses={"www.tikwm.com/api/": {"code": 0, "data": {"title": "synthetic"}}})
    with pytest.raises(MediaExtractionError, match="video metadata"):
        service.extract_sync("https://www.tiktok.com/@synthetic/video/123", http)


@pytest.mark.unit
def test_sync_transport_error_raises_media_extraction_error() -> None:
    """HTTP transport failure maps to MediaExtractionError in synchronous worker extraction."""
    service = TwitterService.sync()
    http = FakeSyncHTTP()
    with pytest.raises(MediaExtractionError, match="request failed"):
        service.extract_sync("https://x.com/user/status/123", http)


@pytest.mark.unit
def test_sync_extract_media_exhausted_strategies_raises() -> None:
    """When all synchronous extraction tiers fail, MediaExtractionError is raised."""
    service = TwitterService.sync()
    http = FakeSyncHTTP()

    class FailingExtractor(NativeMediaExtractor):
        def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
            msg = "generic failure"
            raise MediaExtractionError(msg)

    with pytest.raises(MediaExtractionError, match="strategies are exhausted"):
        service.extract_media_sync("https://x.com/user/status/123", http, FailingExtractor())
