"""Verify the native vxinstagram adapter against synthetic public-service responses."""

from http import HTTPStatus

import pytest

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.embed import build_status_url
from hovorunv2.application.media.instagram.results import InstagramPost
from hovorunv2.infrastructure.media.embed_adapter import VxInstagramEmbedAdapter
from tests.utils.sync_http import HTTPFixture, HTTPReply, json_reply

POST = InstagramPost("Synthetic_1", "p")
IMAGE = "https://media.example.invalid/photo.jpg"
VIDEO = "https://media.example.invalid/video.mp4"


def _embed(*attachments: dict[str, object], url: str = POST.canonical_url) -> dict[str, object]:
    return {
        "url": url,
        "content": "<p>Synthetic caption</p>",
        "media_attachments": list(attachments),
        "account": {"display_name": "Synthetic User", "username": "synthetic"},
    }


def _adapter_and_routes(raw: object) -> tuple[VxInstagramEmbedAdapter, HTTPFixture, str]:
    path = build_status_url(POST, base_url="")
    server = HTTPFixture({path: raw if isinstance(raw, HTTPReply) else json_reply(raw)})
    return VxInstagramEmbedAdapter(base_url=server.url("")), server, server.url("")


def test_adapter_maps_photo_response() -> None:
    """A single public image maps to a complete ordered candidate."""
    adapter, server, _ = _adapter_and_routes(_embed({"type": "image", "url": IMAGE}))
    with server:
        result = adapter.extract(POST, ExtractionDeadline.after(5))
    assert result.complete
    assert [slide.url for slide in result.slides] == [IMAGE]
    assert not result.slides[0].is_video


def test_adapter_maps_video_response() -> None:
    """A single public video keeps video classification and the real source URL."""
    adapter, server, _ = _adapter_and_routes(_embed({"type": "video", "url": VIDEO, "preview_url": IMAGE}))
    with server:
        result = adapter.extract(POST, ExtractionDeadline.after(5))
    assert result.complete
    assert result.slides[0].is_video
    assert result.slides[0].url == VIDEO


def test_adapter_two_item_album_is_complete_below_cap() -> None:
    """Below the four-item rotation cap, the count proves completion."""
    adapter, server, _ = _adapter_and_routes(_embed({"type": "image", "url": IMAGE}, {"type": "video", "url": VIDEO}))
    with server:
        result = adapter.extract(POST, ExtractionDeadline.after(5))
    assert result.complete
    assert len(result.slides) == 2


def test_adapter_four_item_response_is_capped_not_complete() -> None:
    """A four-item response may be rotation-capped and cannot claim completion."""
    attachments = [{"type": "image", "url": IMAGE} for _ in range(4)]
    adapter, server, _ = _adapter_and_routes(_embed(*attachments))
    with server:
        result = adapter.extract(POST, ExtractionDeadline.after(5))
    assert len(result.slides) == 4
    assert not result.complete


def test_adapter_rejects_html_challenge() -> None:
    """An HTML challenge page is not a valid public JSON status response."""
    path = build_status_url(POST, base_url="")
    server = HTTPFixture({path: HTTPReply(b"<html>sign in</html>", headers={"Content-Type": "text/html"})})
    adapter = VxInstagramEmbedAdapter(base_url=server.url(""))
    with server, pytest.raises(MediaExtractionError):
        adapter.extract(POST, ExtractionDeadline.after(5))


def test_adapter_rejects_status_failure() -> None:
    """A 404 status fails recoverably instead of producing a false candidate."""
    path = build_status_url(POST, base_url="")
    server = HTTPFixture({path: HTTPReply(status=HTTPStatus.NOT_FOUND)})
    adapter = VxInstagramEmbedAdapter(base_url=server.url(""))
    with server, pytest.raises(MediaExtractionError):
        adapter.extract(POST, ExtractionDeadline.after(5))


def test_adapter_rejects_response_for_another_post() -> None:
    """A status response identifying another shortcode is not usable."""
    other = InstagramPost("OtherPost", "p")
    adapter, server, _ = _adapter_and_routes(_embed({"type": "image", "url": IMAGE}, url=other.canonical_url))
    with server, pytest.raises(MediaExtractionError):
        adapter.extract(POST, ExtractionDeadline.after(5))


def test_adapter_rejects_text_only_and_unsafe_media() -> None:
    """A response without usable public media raises instead of fabricating slides."""
    adapter, server, _ = _adapter_and_routes(_embed({"type": "image", "url": "http://127.0.0.1/photo.jpg"}))
    with server, pytest.raises(MediaExtractionError):
        adapter.extract(POST, ExtractionDeadline.after(5))
