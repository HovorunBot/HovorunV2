"""Verify Instagram browser carousel traversal decisions without launching Chromium."""

import pytest

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.browser_extraction import (
    CAROUSEL_NEXT_SELECTOR,
    extract_browser_post,
)
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


class _NextButton:
    """Advance the fake carousel by one bounded step."""

    def __init__(self, tab: _CarouselTab) -> None:
        self._tab = tab

    def click(self) -> object:
        self._tab.advance()
        return None


class _CarouselTab:
    """Duck-typed browser tab exposing only the operations used by carousel extraction."""

    def __init__(
        self,
        slides: list[dict[str, object]],
        *,
        structured: dict[str, object] | None = None,
        wrap: bool = False,
    ) -> None:
        self._slides = slides
        self._structured = structured
        self._wrap = wrap
        self._index = 0
        self.clicks = 0

    def run_js(self, script: str) -> object:
        if "shortcode_media" in script:
            return self._structured
        if "data-ig-slide-active" in script:
            if not self._slides or self._index >= len(self._slides):
                return None
            return self._slides[self._index]
        return None

    def ele(self, locator: str, *, timeout: float) -> _NextButton | None:
        if locator != CAROUSEL_NEXT_SELECTOR:
            return None
        if not self._wrap and self._index + 1 >= len(self._slides):
            return None
        return _NextButton(self)

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
