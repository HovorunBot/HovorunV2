"""Enumerate Instagram browser carousels from structured data and bounded traversal."""

import json
from dataclasses import dataclass
from operator import attrgetter
from time import monotonic, sleep

from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.browser_scripts import ADVANCE_SLIDE_JS, OBSERVE_SLIDE_JS, STRUCTURED_POST_JS
from hovorunv2.application.media.instagram.parsing import parse_browser_documents
from hovorunv2.application.media.instagram.results import (
    InstagramPost,
    InstagramResult,
    InstagramSlide,
    order_observations,
)
from hovorunv2.application.media.limits import MAX_PLAYLIST_ITEMS
from hovorunv2.utils.metadata import extract_og_metadata

CAROUSEL_SELECTOR_TIMEOUT_SECONDS = 1.0
MAX_CAROUSEL_STEPS = MAX_PLAYLIST_ITEMS
SLIDE_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class _SlideObservation:
    """One bounded DOM observation before an optional traversal advance."""

    position: int
    slide: InstagramSlide | None
    is_video: bool


def _carousel_complete(slides: dict[int, InstagramSlide], expected: int | None, *, terminal: bool) -> bool:
    """Require contiguous positions plus a count agreement or a verified terminal.

    Args:
        slides: Observed slides keyed by original position.
        expected: Authoritative count, when known.
        terminal: Whether the traversal verified no further slide exists.

    Returns:
        Whether the current observations prove a complete album.
    """
    positions = sorted(slides)
    contiguous = positions == list(range(len(positions)))
    if not contiguous:
        return False
    if expected is not None:
        return len(slides) == expected
    return terminal


def _read_structured_post(tab: BrowserTab) -> list[object]:
    """Return decoded requested-post nodes without retaining page diagnostics."""
    value = tab.run_js(STRUCTURED_POST_JS)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _observe_slide(tab: BrowserTab, post: InstagramPost) -> _SlideObservation | None:
    """Read the currently visible slide facts or report an unavailable observation."""
    raw = tab.run_js(OBSERVE_SLIDE_JS.replace("__SHORTCODE__", json.dumps(post.shortcode)))
    if not isinstance(raw, dict):
        return None
    position = raw.get("position")
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        return None
    is_video = raw.get("is_video")
    if not isinstance(is_video, bool):
        return None
    identity = raw.get("id")
    if not isinstance(identity, str) or not identity:
        identity = f"{post.shortcode}:{position}"
    url = raw.get("url")
    url_text = url if isinstance(url, str) else ""
    try:
        slide = InstagramSlide(identity, position, url_text, is_video)
    except MediaExtractionError:
        return _SlideObservation(position, None, is_video)
    return _SlideObservation(position, slide, is_video)


def _advance(tab: BrowserTab, post: InstagramPost) -> bool:
    """Advance only the requested article's enabled carousel control."""
    return tab.run_js(ADVANCE_SLIDE_JS.replace("__SHORTCODE__", json.dumps(post.shortcode))) is True


def _wait_for_slide(tab: BrowserTab, post: InstagramPost, previous: int) -> _SlideObservation | None:
    """Wait for a bounded position change rather than reading immediately after a click."""
    expires = monotonic() + CAROUSEL_SELECTOR_TIMEOUT_SECONDS
    while True:
        observation = _observe_slide(tab, post)
        if observation is not None and observation.position != previous:
            return observation
        remaining = expires - monotonic()
        if remaining <= 0:
            return observation
        sleep(min(SLIDE_POLL_SECONDS, remaining))


def _traverse_carousel(tab: BrowserTab, post: InstagramPost, initial: InstagramResult | None) -> InstagramResult:
    """Fill lazy slides with bounded forward traversal, stopping on terminal, loop or stall."""
    slides = {slide.position: slide for slide in initial.slides} if initial is not None else {}
    expected = initial.expected_count if initial is not None else None
    terminal = initial.terminal_verified if initial is not None else False
    video_unavailable = initial.video_unavailable if initial is not None else False
    traversed: set[int] = set()

    observation = _observe_slide(tab, post)
    for _ in range(MAX_CAROUSEL_STEPS + 1):
        if _carousel_complete(slides, expected, terminal=terminal):
            break
        if observation is None:
            break
        if observation.position in traversed:
            # A repeated position without completion means a loop or stalled advance.
            break
        traversed.add(observation.position)
        if observation.slide is not None and observation.position not in slides:
            slides[observation.position] = observation.slide
        elif observation.slide is None and observation.is_video:
            video_unavailable = True
        if not _advance(tab, post):
            terminal = True
            break
        observation = _wait_for_slide(tab, post, observation.position)
    return _build_result(post, slides, expected, initial, terminal=terminal, video_unavailable=video_unavailable)


def _build_result(
    post: InstagramPost,
    slides: dict[int, InstagramSlide],
    expected: int | None,
    initial: InstagramResult | None,
    *,
    terminal: bool,
    video_unavailable: bool,
) -> InstagramResult:
    ordered = order_observations(sorted(slides.values(), key=attrgetter("position")))
    if initial is not None:
        return InstagramResult(
            post=post,
            slides=ordered,
            expected_count=expected,
            terminal_verified=terminal,
            author_name=initial.author_name,
            author_handle=initial.author_handle,
            content=initial.content,
            video_unavailable=video_unavailable,
        )
    return InstagramResult(
        post=post,
        slides=ordered,
        expected_count=expected,
        terminal_verified=terminal,
        video_unavailable=video_unavailable,
    )


def extract_browser_post(tab: BrowserTab, post: InstagramPost) -> InstagramResult:
    """Prefer complete structured data, otherwise traverse the requested carousel.

    Args:
        tab: Bounded browser tab for one synchronous extraction.
        post: Canonical requested Instagram identity.

    Returns:
        Complete or retained partial carousel candidate. Raises MediaExtractionError
        when no usable requested-post media remains.
    """
    initial: InstagramResult | None = None
    structured = _read_structured_post(tab)
    if structured:
        try:
            initial = parse_browser_documents(structured, post)
        except MediaExtractionError:
            initial = None
    if initial is not None and initial.complete:
        return initial
    try:
        return _traverse_carousel(tab, post, initial)
    except MediaExtractionError:
        return _opengraph_candidate(tab, post)


def _opengraph_candidate(tab: BrowserTab, post: InstagramPost) -> InstagramResult:
    """Retain an OpenGraph preview as partial media, never as a complete album or text-only success."""
    metadata = extract_og_metadata(tab.html)
    original = metadata.get("url")
    if original is not None and InstagramPost.from_url(original).shortcode != post.shortcode:
        msg = "Instagram preview identifies another post."
        raise MediaExtractionError(msg)
    video = metadata.get("video") or metadata.get("video:url") or metadata.get("video:secure_url")
    if not video and post.kind in {"reel", "reels", "tv"}:
        msg = "Instagram video preview has no video source."
        raise MediaExtractionError(msg)
    url = video or metadata.get("image", "")
    return InstagramResult(
        post=post,
        slides=(InstagramSlide(f"{post.shortcode}:preview", 0, url, is_video=bool(video)),),
        content=metadata.get("description") or metadata.get("title") or "Instagram Post",
    )
