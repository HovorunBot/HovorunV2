"""Real-Chromium Instagram carousel enumeration and bounded download contracts."""

import pytest

from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.browser_extraction import extract_browser_post
from hovorunv2.application.media.instagram.results import InstagramPost
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService
from tests.utils.instagram_pages import carousel_page
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = [pytest.mark.browser]

SHORTCODE = "Synthetic_1"


def _media_url(position: int, *, video: bool = False) -> str:
    suffix = "mp4" if video else "jpg"
    return f"https://media.example.invalid/{position}.{suffix}"


def _extract_carousel(tab: BrowserTab, url: str) -> RichMediaPayload | None:
    post = InstagramPost(SHORTCODE, "p")
    return extract_browser_post(tab, post).to_payload()


def _extract_loopback_images(tab: BrowserTab, url: str) -> RichMediaPayload | None:
    urls = tab.run_js("return Array.from(document.images, image => image.src)")
    assert isinstance(urls, list)
    assert all(isinstance(value, str) for value in urls)
    return RichMediaPayload(
        author_name="Synthetic",
        author_handle="",
        author_url="",
        content="",
        original_url=url,
        media_items=[MediaItem(value, is_video=False) for value in urls],
    )


def _serve(page: str) -> tuple[HTTPFixture, str]:
    server = HTTPFixture({"/post": HTTPReply(page.encode(), headers={"Content-Type": "text/html"})})
    return server, server.url("/post")


def _slides(count: int, *, videos: set[int] | None = None) -> list[tuple[str, bool]]:
    video_positions = videos or set()
    return [
        (_media_url(position, video=position in video_positions), position in video_positions)
        for position in range(count)
    ]


@pytest.mark.slow
def test_browser_complete_structured_album_in_order() -> None:
    """Structured carousel data yields the full ordered album without traversal misses."""
    slides = _slides(3)
    server, url = _serve(carousel_page(SHORTCODE, slides))
    with server:
        service = NativeBrowserService()
        try:
            payload, _ = service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
            assert [item.url for item in payload.media_items] == [_media_url(position) for position in range(3)]
            assert [item.is_video for item in payload.media_items] == [False, False, False]
            assert payload.footer_text == ""
        finally:
            service.close()


@pytest.mark.slow
def test_browser_lazy_carousel_enumerates_remaining_slides() -> None:
    """Lazy slides outside the structured prefix are collected by bounded traversal."""
    slides = _slides(5)
    server, url = _serve(carousel_page(SHORTCODE, slides, structured_observed=2, advance_delay_ms=100))
    with server:
        service = NativeBrowserService()
        try:
            payload, _ = service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
            assert [item.url for item in payload.media_items] == [_media_url(position) for position in range(5)]
            assert payload.footer_text == ""
        finally:
            service.close()


@pytest.mark.slow
def test_browser_dom_traversal_excludes_other_articles_and_profile_images() -> None:
    """Resolve the requested article before observing media or advancing a carousel."""
    document = carousel_page(SHORTCODE, _slides(3), structured_observed=0, advance_delay_ms=100)
    unrelated = (
        '<article><a href="https://www.instagram.com/p/OtherPost/">Other post</a>'
        '<ul><li><img src="https://media.example.invalid/unrelated.jpg"></li></ul>'
        '<button aria-label="Next">Next</button></article>'
    )
    avatar = '<header><img src="https://media.example.invalid/avatar.jpg"></header>'
    document = document.replace("<body>", "<body>" + unrelated).replace(
        '<article id="synthetic-post">', '<article id="synthetic-post">' + avatar
    )
    server, url = _serve(document)
    with server:
        service = NativeBrowserService()
        try:
            payload, _ = service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
            assert [item.url for item in payload.media_items] == [_media_url(position) for position in range(3)]
            assert payload.footer_text == ""
        finally:
            service.close()


@pytest.mark.slow
def test_browser_seventeen_slide_album_above_embed_cap() -> None:
    """Seventeen lazy slides exceed the embed cap and are fully enumerated by traversal."""
    slides = _slides(17)
    server, url = _serve(carousel_page(SHORTCODE, slides, structured_observed=3))
    with server:
        service = NativeBrowserService()
        try:
            payload, _ = service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
            assert len(payload.media_items) == 17
            assert [item.url for item in payload.media_items] == [_media_url(position) for position in range(17)]
            assert payload.footer_text == ""
        finally:
            service.close()


@pytest.mark.slow
def test_browser_mixed_album_preserves_video_order() -> None:
    """Mixed image and video slides keep original positions and video classification."""
    slides = _slides(4, videos={1, 3})
    server, url = _serve(carousel_page(SHORTCODE, slides))
    with server:
        service = NativeBrowserService()
        try:
            payload, _ = service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
            assert [item.is_video for item in payload.media_items] == [False, True, False, True]
            assert [item.url for item in payload.media_items] == [slide_url for slide_url, _ in slides]
        finally:
            service.close()


@pytest.mark.slow
def test_browser_wrapped_carousel_retains_partial_not_complete() -> None:
    """A looping carousel without an authoritative count cannot claim completion."""
    slides = _slides(3)
    server, url = _serve(carousel_page(SHORTCODE, slides, structured_observed=0, loop=True))
    with server:
        service = NativeBrowserService()
        try:
            payload, _ = service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
            assert len(payload.media_items) == 3
            assert payload.footer_text
        finally:
            service.close()


@pytest.mark.slow
def test_browser_challenge_page_raises_safe_failure() -> None:
    """A page without requested-post media raises instead of returning a false candidate."""
    challenge = "<!DOCTYPE html><html><body>Sign in to continue</body></html>"
    server, url = _serve(challenge)
    with server:
        service = NativeBrowserService()
        try:
            with pytest.raises(MediaExtractionError):
                service.extract_and_download(url, _extract_carousel, deadline=ExtractionDeadline.after(60))
        finally:
            service.close()


@pytest.mark.slow
def test_browser_downloads_carousel_bytes_in_order() -> None:
    """Bounded media downloads preserve slide order and exact bytes."""
    with HTTPFixture({"/0": HTTPReply(b"zero"), "/1": HTTPReply(b"one"), "/2": HTTPReply(b"two")}) as media:
        document = (
            "<html><body>"
            f'<img src="{media.url("/0")}"><img src="{media.url("/1")}"><img src="{media.url("/2")}">'
            "</body></html>"
        )
        with HTTPFixture({"/post": HTTPReply(document.encode(), headers={"Content-Type": "text/html"})}) as server:
            service = NativeBrowserService(allowed_hosts=frozenset({"127.0.0.1", "localhost", "media.example.invalid"}))
            try:
                payload, results = service.extract_and_download(server.url("/post"), _extract_loopback_images)
                assert results == [b"zero", b"one", b"two"]
                assert [item.data for item in payload.media_items] == [b"zero", b"one", b"two"]
            finally:
                service.close()
