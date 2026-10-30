"""Required provider I/O and final strategy failure using real services and external HTTP fixtures."""

import re

import aiohttp
import pytest
from dishka import AsyncContainer

from hovorunv2.application.clients.base import BaseMediaClient
from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.clients.config import ProviderConfig
from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.dtos import RichMediaPayload
from hovorunv2.application.features import TriggerKind
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.pipeline import MediaPipeline
from hovorunv2.application.ports import BrowserPort, CommandStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from tests.conftest import authorize_private_media
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.sync_http import FakeSyncHTTP


@pytest.mark.parametrize(
    ("service_type", "source", "endpoint"),
    [
        (TwitterService, "https://x.com/synthetic/status/123", r"https://api\.vxtwitter\.com/i/status/123"),
        (TikTokService, "https://www.tiktok.com/@synthetic/video/123", r"https://www\.tikwm\.com/api/.*"),
        (
            BlueskyService,
            "https://bsky.app/profile/synthetic.test/post/abc",
            r"https://public\.api\.bsky\.app/xrpc/app\.bsky\.actor\.getProfile.*",
        ),
    ],
)
@pytest.mark.parametrize("response", [None, [], {"unexpected": "synthetic"}])
async def test_unusable_api_data_is_semantic_failure_not_unsupported_capability(
    test_container: AsyncContainer, service_type: type[BaseMediaClient], source: str, endpoint: str, response: object
) -> None:
    """Unsupported input remains optional while a selected required API cannot silently return no result."""
    service = await test_container.get(service_type)
    with RecordedResponses() as http:
        assert await service.extract("https://synthetic.invalid/unsupported") is None
        assert not http.requests
        http.get(re.compile(endpoint), payload=response)
        with pytest.raises(MediaExtractionError):
            await service.extract(source)
        assert len(http.requests) == 1


async def test_malformed_gallery_cannot_be_reinterpreted_as_character_media(test_container: AsyncContainer) -> None:
    """A non-array gallery is rejected before DTO construction instead of producing one URL per character."""
    service = await test_container.get(TikTokService)
    with RecordedResponses() as http:
        http.get(re.compile(r"https://www\.tikwm\.com/api/.*"), payload={"code": 0, "data": {"images": "invalid"}})
        with pytest.raises(MediaExtractionError, match="gallery metadata"):
            await service.extract("https://www.tiktok.com/@synthetic/video/123")


async def test_pipeline_denial_stays_optional_but_exhausted_strategies_raise(test_container: AsyncContainer) -> None:
    """The real access service rejects before work; authorized empty capabilities yield one final semantic failure."""
    client = BaseMediaClient(
        ProviderConfig(
            CommandName.FACEBOOK, re.compile(r"https://synthetic.invalid/post"), post_url_pattern=re.compile(".")
        ),
        await test_container.get(aiohttp.ClientSession),
        await test_container.get(BrowserPort),
    )
    pipeline = await test_container.get(MediaPipeline)
    with RecordedResponses() as http:
        assert (
            await pipeline.process(
                client, "https://synthetic.invalid/post", 101, actor_id=101, trigger=TriggerKind.DIRECT
            )
            is None
        )
        assert not http.requests
        await authorize_private_media(test_container, 101)
        await (await test_container.get(CommandStore)).enable_command(
            101, str(CommandName.FACEBOOK_POST), actor=ActorContext.cli()
        )
        with pytest.raises(MediaExtractionError, match="strategies are exhausted"):
            await pipeline.process(
                client, "https://synthetic.invalid/post", 101, actor_id=101, trigger=TriggerKind.DIRECT
            )
        assert not http.requests


async def test_minimal_valid_provider_response_remains_usable(test_container: AsyncContainer) -> None:
    """Positive control proves the external JSON fixture reaches the actual provider parser and defaults."""
    service = await test_container.get(TwitterService)
    source = "https://x.com/synthetic/status/123"
    with RecordedResponses() as http:
        http.get("https://api.vxtwitter.com/i/status/123", payload={"text": "synthetic post", "media_extended": []})
        payload = await service.extract(source)
        assert payload is not None
        assert payload.content == "synthetic post"
        assert payload.author_name == "Unknown"
        assert payload.original_url == source


@pytest.mark.parametrize("text", [None, 123, []])
async def test_invalid_required_provider_fields_fail_before_rendering(
    test_container: AsyncContainer, text: object
) -> None:
    """Wrong typed fields never become a nominal payload that fails later during escaping or delivery."""
    service = await test_container.get(TwitterService)
    with RecordedResponses() as http:
        http.get("https://api.vxtwitter.com/i/status/123", payload={"text": text})
        with pytest.raises(MediaExtractionError, match="unusable extraction metadata"):
            await service.extract("https://x.com/synthetic/status/123")


async def test_selected_video_provider_requires_media_in_its_success_response(test_container: AsyncContainer) -> None:
    """A successful API code without any rendition is a failed extraction, not a placeholder-only post."""
    service = await test_container.get(TikTokService)
    with RecordedResponses() as http:
        http.get(re.compile(r"https://www\.tikwm\.com/api/.*"), payload={"code": 0, "data": {"title": "synthetic"}})
        with pytest.raises(MediaExtractionError, match="video metadata"):
            await service.extract("https://www.tiktok.com/@synthetic/video/123")


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
    with pytest.raises(MediaExtractionError):
        service.extract_sync(url, http)


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
