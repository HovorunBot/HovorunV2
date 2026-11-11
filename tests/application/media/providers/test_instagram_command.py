"""Tests for Instagram rich media command."""

from http import HTTPStatus

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserContinuationRequired, MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter, build_status_url
from hovorunv2.application.media.instagram.results import InstagramPost
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.application.media.providers.instagram import InstagramService
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.conftest import authorize_private_media, create_private_media_message, get_media_command
from tests.utils.instagram_browser import RoutedInstagramBrowser, route_media_downloads
from tests.utils.instagram_worker import instagram_worker, route_instagram_backend
from tests.utils.jobs import broker_envelopes
from tests.utils.sync_browser import FakeSyncBrowser
from tests.utils.sync_http import FakeSyncHTTP, HTTPFixture, HTTPReply, json_reply
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.telegram.recording_delivery import RecordingTelegramDelivery


@pytest.fixture
async def instagram_command(test_container: AsyncContainer) -> RichMediaCommand:
    """Fixture to provide the Instagram RichMediaCommand instance."""
    return await get_media_command(test_container, CommandName.INSTAGRAM)


async def _assert_instagram_command(
    command: RichMediaCommand,
    container: AsyncContainer,
    bot: Bot,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """Drive real queued extraction and translation using synthetic external HTTP responses."""
    chat_id = 789 if kind == "p" else 790
    post = InstagramPost("Synthetic_1", kind)
    message = create_private_media_message(post.canonical_url, chat_id=chat_id).as_(bot)
    statuses = await container.get(ChatStatusService)
    await statuses.approve(chat_id)
    await authorize_private_media(container, chat_id)
    await command.handle(message, bot)
    settings = await container.get(Settings)
    settings = settings.model_copy(update={"database_url": database_url, "instagram_sessionid": None})
    namespace = await container.get(RuntimeNamespace)
    video = kind != "p"
    raw = {
        "url": post.canonical_url,
        "content": "<p>Synthetic caption</p><p>❤️ 7 💬 2</p>",
        "account": {"display_name": "Mock User", "username": "synthetic"},
        "media_attachments": [{"type": "video" if video else "image", "url": "https://media.example.invalid/item"}],
    }
    routes = {
        "/unavailable": HTTPReply(status=HTTPStatus.NOT_FOUND),
        build_status_url(post, base_url=""): json_reply(raw),
        f"/bot{BOT_CREDENTIAL}/sendMessage": json_reply({"ok": True, "result": {"message_id": 100}}),
    }
    delivery = RecordingTelegramDelivery(bot)
    with HTTPFixture(routes) as server:
        route_instagram_backend(monkeypatch, server)
        with instagram_worker(settings, namespace, delivery, server) as (runner, runtime, _database):
            messages = broker_envelopes(settings, namespace.value)
            assert len(messages) == 1
            assert runner.run(messages[0]) == JobOutcome.COMPLETE.value
            if video:
                compression = broker_envelopes(settings, namespace.value, "compression")
                assert compression == messages
                assert runner.run(compression[0], JobStage.COMPRESSION) == JobOutcome.COMPLETE.value
                assert runner.run(compression[0], JobStage.COMPRESSION) == JobOutcome.COMPLETE.value
            assert runner.run(messages[0]) == JobOutcome.COMPLETE.value
            assert runtime.payloads.load_candidate(str(messages[0]["job_id"])) is None
        assert not any(request.path.startswith("/models/") for request in server.requests)
    assert len(bot.session.calls) == 1
    assert len(delivery.calls) == 1
    payload = delivery.calls[0]["payload"]
    assert isinstance(payload, RichMediaPayload)
    assert payload.is_video is video
    call = bot.session.calls[0]
    caption = call.media[0].caption if hasattr(call, "media") else getattr(call, "caption", "")
    assert "Mock User" in caption
    assert "instagram.com" in caption


async def test_handle_instagram_post(
    instagram_command: RichMediaCommand,
    test_container: AsyncContainer,
    recording_bot: Bot,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extract a photo through real yt-dlp failure, embed fallback, translation and queued delivery."""
    await _assert_instagram_command(
        instagram_command, test_container, recording_bot, postgres_database_url, monkeypatch, "p"
    )


async def test_handle_instagram_reel(
    instagram_command: RichMediaCommand,
    test_container: AsyncContainer,
    recording_bot: Bot,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep video classification through the same synthetic queued fallback pipeline."""
    await _assert_instagram_command(
        instagram_command, test_container, recording_bot, postgres_database_url, monkeypatch, "reel"
    )


@pytest.mark.browser
@pytest.mark.slow
def test_instagram_browser_fallback_uses_real_dom_and_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Download a browser OpenGraph preview through the real provider callback with canonical identity."""
    service = InstagramService.sync(embed=DisabledEmbedAdapter())
    url = "https://www.instagram.com/p/Synthetic_1/"
    image = "https://media.example.invalid/image.jpg"
    document = (
        '<html><head><meta property="og:title" content="Synthetic fallback">'
        f'<meta property="og:url" content="{url}">'
        f'<meta property="og:image" content="{image}">'
        "</head><body><main>Synthetic fallback</main></body></html>"
    )
    with (
        HTTPFixture({"/image.jpg": HTTPReply(b"synthetic image")}) as media,
        HTTPFixture({"/post": HTTPReply(document.encode(), headers={"Content-Type": "text/html"})}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost", "media.example.invalid"})) as http,
    ):
        route_media_downloads(monkeypatch, media)
        browser = RoutedInstagramBrowser(server.url("/post"))
        try:
            outcome = service.extract_browser_outcome_sync(url, http, browser, deadline=ExtractionDeadline.after(20))
            payload = outcome.payload
            assert not outcome.complete
            assert "Synthetic fallback" in payload.content
            assert payload.media_items[0].data == b"synthetic image"
            assert payload.media_items[0].url == image
            assert payload.original_url == url
            assert payload.footer_text
            assert len(media.requests) == 1
        finally:
            browser.close()
        assert not browser.is_running


@pytest.mark.unit
def test_instagram_sync_media_stage_extractor() -> None:
    """Test that yt-dlp success completes in media stage without browser continuation."""
    service = InstagramService.sync(embed=DisabledEmbedAdapter())
    url = "https://www.instagram.com/reel/C1234567890/"
    http = FakeSyncHTTP()

    expected_payload = RichMediaPayload(
        author_name="Instagram Creator",
        author_handle="creator",
        author_url="https://www.instagram.com/creator/",
        content="Sync reel video",
        footer_text="❤️ 100",
        original_url=url,
        media_items=[MediaItem(url="https://scontent.cdninstagram.com/vid.mp4", is_video=True)],
    )

    class ReelExtractor(NativeMediaExtractor):
        def extract(
            self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
        ) -> RichMediaPayload:
            return expected_payload

    payload = service.extract_media_sync(url, http, ReelExtractor())
    assert payload == expected_payload


@pytest.mark.unit
def test_instagram_sync_media_stage_requires_browser_continuation() -> None:
    """Test that when yt-dlp and the embed fallback miss, BrowserContinuationRequired is raised."""
    service = InstagramService.sync(embed=DisabledEmbedAdapter())
    url = "https://www.instagram.com/p/C9876543210/"
    http = FakeSyncHTTP()

    class FailingExtractor(NativeMediaExtractor):
        def extract(
            self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
        ) -> RichMediaPayload:
            msg = "yt-dlp miss"
            raise MediaExtractionError(msg)

    with pytest.raises(BrowserContinuationRequired):
        service.extract_media_sync(url, http, FailingExtractor())


@pytest.mark.unit
def test_instagram_text_only_ytdlp_continues_to_browser() -> None:
    """A text-only/metadata-only generic result is not usable media and continues the chain."""
    service = InstagramService.sync(embed=DisabledEmbedAdapter())
    url = "https://www.instagram.com/p/C9876543210/"
    http = FakeSyncHTTP()

    class TextOnlyExtractor(NativeMediaExtractor):
        def extract(
            self, url: str, cookies: dict[str, str] | None = None, *, deadline: ExtractionDeadline | None = None
        ) -> RichMediaPayload:
            return RichMediaPayload(
                author_name="Creator",
                author_handle="creator",
                author_url="",
                content="text only",
                original_url=url,
            )

    with pytest.raises(BrowserContinuationRequired):
        service.extract_media_sync(url, http, TextOnlyExtractor())


@pytest.mark.unit
def test_instagram_sync_browser_stage_extraction() -> None:
    """Test browser-stage extraction using SyncBrowserPort."""
    service = InstagramService.sync(embed=DisabledEmbedAdapter())
    url = "https://www.instagram.com/p/C9876543210/"
    http = FakeSyncHTTP()

    expected_payload = RichMediaPayload(
        author_name="Browser Creator",
        author_handle="browsercreator",
        author_url="https://www.instagram.com/browsercreator/",
        content="Browser post content",
        footer_text="Instagram Post",
        original_url=url,
        media_items=[MediaItem(url="https://scontent.cdninstagram.com/browser.jpg", is_video=False)],
    )

    browser = FakeSyncBrowser(extract_payloads={url: (expected_payload, [b"image_bytes"])})
    payload = service.extract_browser_media_sync(url, http, browser)
    assert payload.author_name == "Browser Creator"
    assert payload.media_items[0].data == b"image_bytes"


@pytest.mark.unit
def test_instagram_browser_stage_rejects_metadata_only_http_fallback() -> None:
    """A browser miss cannot turn HTTP metadata-only output into successful Instagram media."""
    service = InstagramService.sync(embed=DisabledEmbedAdapter())
    url = "https://www.instagram.com/p/Synthetic_1/"
    http = FakeSyncHTTP(text_responses={url: '<meta property="og:title" content="Synthetic">'})
    with pytest.raises(MediaExtractionError):
        service.extract_browser_media_sync(url, http, FakeSyncBrowser())
    assert not http.requests
