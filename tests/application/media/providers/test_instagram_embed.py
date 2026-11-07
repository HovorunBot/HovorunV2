"""Verify the allowlisted vxinstagram route and the disabled no-request adapter."""

import base64

import pytest

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.embed import DisabledEmbedAdapter, build_status_url
from hovorunv2.application.media.instagram.results import InstagramPost

pytestmark = pytest.mark.unit
POST = InstagramPost("Synthetic_1", "p")


def _decode_context(url: str) -> str:
    encoded = url.rsplit("/", 1)[-1]
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(padded).decode()


def test_build_status_url_encodes_allowlisted_shortcode_and_path() -> None:
    """Only the validated shortcode and canonical path reach the public service."""
    url = build_status_url(POST)
    assert url.startswith("https://vxinstagram.com/api/v1/statuses/")
    assert _decode_context(url) == "Synthetic_1&0&https://instagram.com/p/Synthetic_1/"


def test_build_status_url_drops_tracking_query_and_fragment() -> None:
    """The built route contains no query, fragment or extra identity."""
    assert "?" not in build_status_url(POST)
    assert "#" not in build_status_url(POST)
    assert "tracking" not in build_status_url(POST)


def test_build_status_url_honors_custom_origin() -> None:
    """Tests can point the same route at a synthetic loopback server."""
    assert build_status_url(POST, base_url="http://127.0.0.1:9").startswith("http://127.0.0.1:9/api/v1/statuses/")


def test_disabled_adapter_raises_without_request() -> None:
    """A disabled deployment fails recoverably instead of opening a request."""
    with pytest.raises(MediaExtractionError, match="disabled"):
        DisabledEmbedAdapter().extract(POST, ExtractionDeadline.after(5))
