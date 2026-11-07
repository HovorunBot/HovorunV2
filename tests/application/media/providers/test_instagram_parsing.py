"""Verify post-scoped complete media, conservative previews and partial selection."""

from dataclasses import replace

import pytest

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.parsing import parse_browser_documents, parse_embed_response
from hovorunv2.application.media.instagram.results import (
    INCOMPLETE_MEDIA_TEXT,
    InstagramPost,
    InstagramResult,
    InstagramSlide,
    order_observations,
    require_public_media_url,
    select_candidate,
)
from hovorunv2.application.media.limits import MAX_PLAYLIST_ITEMS

pytestmark = pytest.mark.unit
POST = InstagramPost("Synthetic_1", "p")
IMAGE = "https://media.example.invalid/image.jpg"
VIDEO = "https://media.example.invalid/video.mp4"


def _slide(position: int, *, video: bool = False) -> InstagramSlide:
    return InstagramSlide(f"slide-{position}", position, VIDEO if video else IMAGE, video)


def _album(count: int, *, observed: int | None = None) -> dict[str, object]:
    size = count if observed is None else observed
    return {
        "shortcode": POST.shortcode,
        "__typename": "GraphSidecar",
        "edge_sidecar_to_children": {
            "count": count,
            "edges": [
                {"node": {"id": f"slide-{position}", "__typename": "GraphImage", "display_url": IMAGE}}
                for position in range(size)
            ],
        },
    }


def _embed(count: int) -> dict[str, object]:
    return {
        "url": POST.canonical_url,
        "content": "<p>Synthetic &amp; caption</p><b>metrics</b>",
        "media_attachments": [
            {"id": POST.shortcode, "type": "image", "url": IMAGE, "preview_url": "https://avatar.invalid/image"}
            for _ in range(count)
        ],
    }


@pytest.mark.parametrize("kind", ["p", "reel", "reels", "tv"])
def test_post_canonicalization_drops_tracking_and_username_prefix(kind: str) -> None:
    """Only the public post identity is forwarded to third-party backends."""
    post = InstagramPost.from_url(f"http://instagram.com/synthetic/{kind}/Synthetic_1/?tracking=private#fragment")
    assert post == InstagramPost("Synthetic_1", kind)
    assert post.canonical_url == f"https://www.instagram.com/{kind}/Synthetic_1/"


@pytest.mark.parametrize(
    "url",
    [
        "https://instagram.com.evil.invalid/p/Synthetic_1/",
        "https://user:secret@instagram.com/p/Synthetic_1/",
        "https://instagram.com/p/Synthetic_1/extra",
        "https://instagram.com/p/",
        "https://instagram.com/profile/",
        "https://instagram.com:wrong/p/Synthetic_1/",
        "https://instagram.com/p/synthetic%26private/",
    ],
)
def test_invalid_post_links_raise_without_retaining_input(url: str) -> None:
    """Invalid identity is an explicit safe failure, not a third-party request target."""
    with pytest.raises(MediaExtractionError, match="identity is invalid") as failure:
        InstagramPost.from_url(url)
    assert url not in str(failure.value)


@pytest.mark.parametrize("count", [1, 3, 5, 17, MAX_PLAYLIST_ITEMS])
def test_browser_album_preserves_distinct_slides_with_identical_urls(count: int) -> None:
    """The extraction cap is independent of Telegram's per-batch limit and embed previews."""
    result = parse_browser_documents([{"graphql": {"shortcode_media": _album(count)}}], POST)
    assert result.complete
    assert len(result.slides) == count
    assert [slide.position for slide in result.slides] == list(range(count))
    assert all(slide.url == IMAGE for slide in result.slides)
    assert result.to_payload().original_url == POST.canonical_url


def test_browser_never_takes_unrelated_post_or_profile_media() -> None:
    """Requested-post matching wins over earlier neighboring images and recommendations."""
    unrelated = {"shortcode": "OtherPost", "__typename": "GraphImage", "display_url": "https://wrong.invalid/a"}
    target = {"shortcode": POST.shortcode, "__typename": "GraphVideo", "video_url": VIDEO, "display_url": IMAGE}
    result = parse_browser_documents([{"avatar": IMAGE, "recommendations": [unrelated], "target": target}], POST)
    assert result.complete
    assert result.slides[0].is_video
    assert result.slides[0].url == VIDEO


def test_requested_scope_does_not_search_unrelated_nested_media() -> None:
    """A matching but empty post cannot borrow its recommendation's usable media."""
    target = {"shortcode": POST.shortcode, "recommendation": {"display_url": IMAGE}}
    with pytest.raises(MediaExtractionError, match="no usable requested-post"):
        parse_browser_documents([target], POST)


def test_contradictory_post_identifiers_do_not_match_requested_scope() -> None:
    """A conflicting identity cannot redirect requested-post parsing to adjacent content."""
    target = {"shortcode": "AnotherPost", "code": POST.shortcode, "display_url": IMAGE}
    with pytest.raises(MediaExtractionError, match="no usable requested-post"):
        parse_browser_documents([target], POST)


def test_browser_mixed_album_preserves_video_and_original_positions() -> None:
    """iPhone-shaped browser facts retain mixed media order and caption defaults."""
    target = {
        "code": POST.shortcode,
        "media_type": 8,
        "carousel_media_count": 3,
        "caption": {"text": "Synthetic #hashtag caption"},
        "carousel_media": [
            {"pk": "a", "media_type": 1, "image_versions2": {"candidates": [{"url": IMAGE}]}},
            {"pk": "b", "media_type": 2, "video_versions": [{"url": VIDEO}], "display_url": IMAGE},
            {"pk": "c", "media_type": 1, "display_url": IMAGE},
        ],
    }
    result = parse_browser_documents([target], POST)
    assert result.complete
    assert [slide.is_video for slide in result.slides] == [False, True, False]
    assert result.content == "Synthetic caption"
    assert result.author_name == "Instagram User"


def test_missing_video_url_never_delivers_its_poster_as_photo() -> None:
    """Missing required video facts leave a gap rather than creating a successful image."""
    target = {
        "shortcode": POST.shortcode,
        "edge_sidecar_to_children": {
            "count": 3,
            "edges": [
                {"node": {"id": "a", "display_url": IMAGE}},
                {"node": {"id": "b", "is_video": True, "display_url": IMAGE}},
                {"node": {"id": "c", "display_url": IMAGE}},
            ],
        },
    }
    result = parse_browser_documents([target], POST)
    assert not result.complete
    assert [slide.position for slide in result.slides] == [0, 2]
    assert INCOMPLETE_MEDIA_TEXT in result.to_payload().footer_text
    assert result.to_payload().is_video
    assert result.to_payload().video_unavailable


@pytest.mark.parametrize("observed", [1, MAX_PLAYLIST_ITEMS])
def test_known_missing_or_over_limit_slides_are_partial(observed: int) -> None:
    """A usable prefix does not claim an album above the item bound is complete."""
    result = parse_browser_documents([_album(MAX_PLAYLIST_ITEMS + 1, observed=observed)], POST)
    assert not result.complete
    assert len(result.slides) == observed


def test_oversized_observation_is_bounded_without_losing_partial() -> None:
    """A structured array larger than the extraction cap retains only safe bounded media."""
    result = parse_browser_documents([_album(MAX_PLAYLIST_ITEMS + 1)], POST)
    assert len(result.slides) == MAX_PLAYLIST_ITEMS
    assert not result.complete


@pytest.mark.parametrize("count", [None, False, 0, -1, "3", 1.5])
def test_unknown_and_invalid_counts_never_prove_completion(count: object) -> None:
    """Absent count remains unknown; invalid counts are not silently trusted."""
    target = {
        "shortcode": POST.shortcode,
        "edge_sidecar_to_children": {"count": count, "edges": [{"node": {"display_url": IMAGE}}]},
    }
    if count is None:
        assert not parse_browser_documents([target], POST).complete
        return
    with pytest.raises(MediaExtractionError):
        parse_browser_documents([target], POST)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_embed_cap_and_repeated_attachment_ids(count: int) -> None:
    """Repeated post IDs keep every attachment; a full four-item response may be capped."""
    result = parse_embed_response(_embed(count), POST)
    assert len(result.slides) == count
    assert result.complete is (count < 4)
    assert result.content == "Synthetic & caption metrics"
    assert result.to_payload().original_url == POST.canonical_url


def test_embed_video_prefers_actual_url_over_preview_and_account_avatar() -> None:
    """Only the public attachment's typed source becomes media."""
    raw = {
        "url": POST.canonical_url,
        "media_attachments": [{"type": "video", "url": VIDEO, "preview_url": IMAGE}],
        "account": {"avatar": IMAGE},
    }
    result = parse_embed_response(raw, POST)
    assert result.slides[0].url == VIDEO
    assert result.slides[0].is_video


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "<html>login</html>",
        {"url": POST.canonical_url, "content": "Text only"},
        {"url": POST.canonical_url, "media_attachments": [None]},
        {"url": POST.canonical_url, "media_attachments": [{"type": ["image"], "url": IMAGE}]},
        {"url": POST.canonical_url, "media_attachments": [{"type": "video", "preview_url": IMAGE}]},
        {"url": "https://instagram.com/p/AnotherPost/", "media_attachments": [{"type": "image", "url": IMAGE}]},
    ],
)
def test_malformed_or_text_only_embed_is_not_usable_media(raw: object) -> None:
    """Invalid required media raises a safe extraction failure rather than a sentinel."""
    with pytest.raises(MediaExtractionError):
        parse_embed_response(raw, POST)


def test_embed_does_not_invent_support_for_more_than_four_attachments() -> None:
    """An unexpected API shape cannot silently change completeness evidence."""
    with pytest.raises(MediaExtractionError, match="invalid attachments"):
        parse_embed_response(_embed(5), POST)


def test_embed_invalid_attachment_retains_other_usable_media_as_partial() -> None:
    """An invalid slide remains missing even when the response is below its cap."""
    raw = {
        "url": POST.canonical_url,
        "media_attachments": [{"type": "image", "url": IMAGE}, {"type": "video", "url": "http://unsafe.invalid/a"}],
    }
    result = parse_embed_response(raw, POST)
    assert len(result.slides) == 1
    assert result.expected_count == 2
    assert not result.complete


def test_observation_deduplication_uses_identity_and_position_not_url() -> None:
    """Repeated visits collapse while distinct slides sharing a CDN URL remain separate."""
    first, second = _slide(0), _slide(1)
    changed_url = replace(first, url="https://media.example.invalid/refreshed.jpg")
    assert order_observations([second, first, changed_url, second]) == (first, second)


def test_conflicting_positions_are_rejected() -> None:
    """Two different identities at one claimed position cannot prove ordered completeness."""
    with pytest.raises(MediaExtractionError, match="observations conflict"):
        order_observations([_slide(0), InstagramSlide("different", 0, IMAGE, is_video=False)])


def test_candidate_selection_prefers_complete_then_largest_and_earliest_tie() -> None:
    """Later misses or smaller partials must not erase the best prior usable attempt."""
    first = InstagramResult(POST, (_slide(0), _slide(1)), expected_count=5)
    smaller = InstagramResult(POST, (_slide(0),), expected_count=5)
    tie = replace(first, content="Later equal candidate")
    larger = InstagramResult(POST, (_slide(0), _slide(1), _slide(2)), expected_count=5)
    complete = InstagramResult(POST, (_slide(0),), expected_count=1)
    assert select_candidate(None, first) is first
    assert select_candidate(first, smaller) is first
    assert select_candidate(first, tie) is first
    assert select_candidate(first, larger) is larger
    assert select_candidate(larger, complete) is complete
    assert select_candidate(complete, larger) is complete


def test_candidate_selection_rejects_another_post() -> None:
    """Retained candidates cannot be attached to another job's public post."""
    candidate = InstagramResult(POST, (_slide(0),))
    other = replace(candidate, post=InstagramPost("OtherPost", "p"))
    with pytest.raises(MediaExtractionError, match="same post"):
        select_candidate(candidate, other)


def test_only_verified_terminal_with_contiguous_positions_proves_unknown_count() -> None:
    """A terminal observation cannot hide missing slides before the observed end."""
    candidate = InstagramResult(POST, (_slide(0), _slide(1)))
    assert not candidate.complete
    assert replace(candidate, terminal_verified=True).complete
    assert not InstagramResult(POST, (_slide(0), _slide(2)), terminal_verified=True).complete


@pytest.mark.parametrize("count", [0, -1, False, 1])
def test_inconsistent_result_count_is_rejected(count: int) -> None:
    """Construction establishes a required usable candidate rather than repeated caller checks."""
    with pytest.raises(MediaExtractionError, match="count is inconsistent"):
        InstagramResult(POST, (_slide(0), _slide(1)), expected_count=count)


@pytest.mark.parametrize(
    "url",
    [
        "http://media.invalid/a",
        "file:///tmp/media",
        "https://localhost/a",
        "https://service.local/a",
        "https://127.0.0.1/a",
        "https://169.254.169.254/a",
        "https://[::1]/a",
        "https://user:password@media.invalid/a",
        "https://media.invalid:80/a",
        "https://media.invalid/a\nsecret",
        "https://media.invalid:bad/a",
    ],
)
def test_obviously_unsafe_media_urls_are_rejected_without_payload_diagnostics(url: str) -> None:
    """Structural validation precedes later DNS/redirect enforcement at the transport boundary."""
    with pytest.raises(MediaExtractionError) as failure:
        require_public_media_url(url)
    assert url not in str(failure.value)


def test_candidate_repr_excludes_caption_post_and_signed_urls() -> None:
    """Accidental dataclass diagnostics reveal only structural extraction facts."""
    result = InstagramResult(POST, (_slide(0),), content="Private synthetic caption")
    representation = repr(result) + repr(result.slides[0]) + repr(POST)
    assert POST.shortcode not in representation
    assert IMAGE not in representation
    assert result.content not in representation


def test_deep_unrelated_json_has_an_explicit_safe_bound() -> None:
    """Iterative lookup cannot recurse indefinitely through an unrelated page document."""
    document: object = {"unrelated": IMAGE}
    for _ in range(40):
        document = {"nested": document}
    with pytest.raises(MediaExtractionError, match="traversal bounds"):
        parse_browser_documents([document], POST)
