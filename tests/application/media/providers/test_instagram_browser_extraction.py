"""Verify Instagram browser carousel traversal decisions without launching Chromium."""

import pytest

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.browser_extraction import extract_browser_post
from hovorunv2.application.media.instagram.results import InstagramPost
from hovorunv2.application.media.limits import MAX_PLAYLIST_ITEMS

pytestmark = pytest.mark.unit
POST = InstagramPost("Synthetic_1", "p")


def _slide(position: int, *, video: bool = False) -> dict[str, object]:
    suffix = "mp4" if video else "jpg"
    return {
        "position": position,
        "id": f"slide-{position}",
        "url": f"https://media.example.invalid/{position}.{suffix}",
        "is_video": video,
    }


def _album(count: int, *, observed: int | None = None) -> dict[str, object]:
    size = count if observed is None else observed
    return {
        "shortcode": POST.shortcode,
        "__typename": "GraphSidecar",
        "edge_sidecar_to_children": {
            "count": count,
            "edges": [
                {
                    "node": {
                        "id": f"slide-{position}",
                        "__typename": "GraphImage",
                        "display_url": f"https://media.example.invalid/{position}.jpg",
                    }
                }
                for position in range(size)
            ],
        },
    }


class _CarouselTab:
    """Duck-typed browser tab exposing only the operations used by carousel extraction."""

    def __init__(
        self,
        slides: list[dict[str, object]],
        *,
        structured: dict[str, object] | None = None,
        wrap: bool = False,
        html: str = "<html></html>",
    ) -> None:
        self._slides = slides
        self._structured = structured
        self._wrap = wrap
        self._index = 0
        self.clicks = 0
        self._html = html

    def run_js(self, script: str) -> object:
        if "window._sharedData" in script:
            return self._structured
        if "const rawPosition" in script:
            if not self._slides or self._index >= len(self._slides):
                return None
            return self._slides[self._index]
        if "button.click();" in script:
            if not self._wrap and self._index + 1 >= len(self._slides):
                return False
            self.advance()
            return True
        return None

    @property
    def html(self) -> str:
        """Return an empty challenge document when traversal has no candidate."""
        return self._html

    def advance(self) -> None:
        self.clicks += 1
        self._index = (self._index + 1) % len(self._slides) if self._wrap else self._index + 1


def test_complete_structured_data_skips_traversal() -> None:
    """A complete structured album never opens the carousel controls."""
    tab = _CarouselTab([], structured=_album(3))
    result = extract_browser_post(tab, POST)
    assert result.complete
    assert len(result.slides) == 3
    assert tab.clicks == 0


def test_lazy_traversal_collects_remaining_slides() -> None:
    """Lazy slides outside the structured prefix are enumerated by bounded forward traversal."""
    structured = _album(5, observed=3)
    tab = _CarouselTab([_slide(position) for position in range(5)], structured=structured)
    result = extract_browser_post(tab, POST)
    assert result.complete
    assert [slide.position for slide in result.slides] == [0, 1, 2, 3, 4]
    assert tab.clicks > 0


def test_terminal_without_structured_data_proves_unknown_count() -> None:
    """A finite carousel with no count still reaches a verified terminal and completes."""
    tab = _CarouselTab([_slide(position) for position in range(3)])
    result = extract_browser_post(tab, POST)
    assert result.complete
    assert len(result.slides) == 3
    assert result.terminal_verified


def test_wrapped_carousel_stops_with_partial_not_complete() -> None:
    """A looping carousel without an authoritative count cannot claim completion."""
    tab = _CarouselTab([_slide(position) for position in range(3)], wrap=True)
    result = extract_browser_post(tab, POST)
    assert len(result.slides) == 3
    assert not result.complete
    assert tab.clicks <= MAX_PLAYLIST_ITEMS


def test_video_slide_without_url_is_unavailable_not_a_photo() -> None:
    """A missing video source leaves a gap rather than fabricating a successful image."""
    slides = [
        _slide(0),
        {"position": 1, "id": "slide-1", "url": "", "is_video": True},
        _slide(2),
    ]
    tab = _CarouselTab(slides, structured=_album(3, observed=1))
    result = extract_browser_post(tab, POST)
    assert not result.complete
    assert [slide.position for slide in result.slides] == [0, 2]
    assert result.video_unavailable
    assert result.to_payload().is_video


def test_no_observable_media_raises_without_sentinel() -> None:
    """A challenge page with no requested-post media is an explicit failure."""
    tab = _CarouselTab([], structured={"shortcode": "OtherPost", "display_url": "https://media.example.invalid/x.jpg"})
    with pytest.raises(MediaExtractionError):
        extract_browser_post(tab, POST)


def test_opengraph_preview_is_partial_and_keeps_canonical_identity() -> None:
    """A rendered OpenGraph image remains an explicitly partial candidate."""
    tab = _CarouselTab([], html='<meta property="og:image" content="https://media.example.invalid/preview.jpg">')
    result = extract_browser_post(tab, POST)
    assert not result.complete
    assert not result.to_payload().complete
    assert result.to_payload().original_url == POST.canonical_url
    assert result.to_payload().footer_text


@pytest.mark.parametrize(
    "metadata",
    [
        '<meta property="og:title" content="Synthetic text only">',
        '<meta property="og:image" content="http://127.0.0.1/private.jpg">',
        (
            '<meta property="og:url" content="https://www.instagram.com/p/OtherPost/">'
            '<meta property="og:image" content="https://media.example.invalid/preview.jpg">'
        ),
    ],
)
def test_unusable_or_wrong_post_opengraph_does_not_become_media(metadata: str) -> None:
    """Text, unsafe targets and another post's preview cannot suppress a retained candidate."""
    with pytest.raises(MediaExtractionError):
        extract_browser_post(_CarouselTab([], html=metadata), POST)


def test_reel_poster_without_video_is_not_successful_photo() -> None:
    """A reel preview image never turns unavailable video into a delivered photo."""
    tab = _CarouselTab([], html='<meta property="og:image" content="https://media.example.invalid/poster.jpg">')
    with pytest.raises(MediaExtractionError):
        extract_browser_post(tab, InstagramPost(POST.shortcode, "reel"))
