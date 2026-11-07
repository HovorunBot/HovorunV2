"""Map post-scoped browser facts and public embed JSON into ordered candidates."""

from collections.abc import Iterable, Iterator
from html.parser import HTMLParser

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.instagram.results import (
    InstagramPost,
    InstagramResult,
    InstagramSlide,
    order_observations,
    select_candidate,
)
from hovorunv2.application.media.limits import MAX_PLAYLIST_ITEMS
from hovorunv2.utils.text import clean_caption_text

EMBED_ATTACHMENT_CAP = 4
MAX_DOCUMENT_NODES = 20_000
MAX_DOCUMENT_DEPTH = 32
IMAGE_TYPE = 1
VIDEO_TYPE = 2
CAROUSEL_TYPE = 8
VIDEO_NAMES = frozenset({"GraphVideo", "XDTGraphVideo"})
IMAGE_NAMES = frozenset({"GraphImage", "XDTGraphImage"})


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _text(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _count(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        msg = "Instagram media count is invalid."
        raise MediaExtractionError(msg)
    return value


class _EmbedCaption(HTMLParser):
    """Read plain caption text without retaining embed HTML or loading external resources."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Collect text while discarding all HTML markup."""
        self._parts.append(data)

    def plain_text(self, value: str) -> str:
        """Normalize embed text and preserve a usable default for an absent caption.

        Args:
            value: Untrusted HTML caption.

        Returns:
            Plain caption suitable for the existing rendering pathway.
        """
        self.feed(value)
        self.close()
        return clean_caption_text(" ".join(self._parts)) or "Instagram Post"


def parse_embed_response(raw: object, post: InstagramPost) -> InstagramResult:
    """Map the public status schema without reproducing the service's media parser.

    Args:
        raw: Decoded public JSON, not HTML or a provider page.
        post: Requested canonical Instagram identity.

    Returns:
        Usable ordered media, conservatively partial at the service's attachment cap.

    Raises:
        MediaExtractionError: If the response is malformed or belongs to another post.
    """
    data = _mapping(raw)
    response_post = InstagramPost.from_url(_text(data.get("url")))
    if response_post.shortcode != post.shortcode:
        msg = "Instagram embed response identifies another post."
        raise MediaExtractionError(msg)
    attachments = _sequence(data.get("media_attachments"))
    if not attachments or len(attachments) > EMBED_ATTACHMENT_CAP:
        msg = "Instagram embed response has invalid attachments."
        raise MediaExtractionError(msg)
    slides = _embed_slides(attachments, post)
    account = _mapping(data.get("account"))
    # Attachment IDs identify the post, not individual slides, in the public API.
    count = len(attachments) if len(attachments) < EMBED_ATTACHMENT_CAP else None
    return InstagramResult(
        post=post,
        slides=slides,
        expected_count=count,
        author_name=_text(account.get("display_name"), "Instagram User"),
        author_handle=_text(account.get("username"), "instagram"),
        content=_EmbedCaption().plain_text(_text(data.get("content"))),
        video_unavailable=_missing_embed_video(attachments, slides),
    )


def _embed_slides(attachments: list[object], post: InstagramPost) -> tuple[InstagramSlide, ...]:
    slides: list[InstagramSlide] = []
    for position, value in enumerate(attachments):
        attachment = _mapping(value)
        media_type = attachment.get("type")
        if media_type not in ("image", "video"):
            continue
        try:
            slide = InstagramSlide(
                identity=f"{post.shortcode}:{position}",
                position=position,
                url=_text(attachment.get("url")),
                is_video=media_type == "video",
            )
        except MediaExtractionError:
            continue
        slides.append(slide)
    return order_observations(slides)


def _missing_embed_video(attachments: list[object], slides: tuple[InstagramSlide, ...]) -> bool:
    usable = {slide.position for slide in slides}
    return any(
        _mapping(item).get("type") == "video" and position not in usable for position, item in enumerate(attachments)
    )


def parse_browser_documents(documents: Iterable[object], post: InstagramPost) -> InstagramResult:
    """Select media only from structured objects identifying the requested post.

    Args:
        documents: Decoded page JSON or browser-extracted structured facts.
        post: Requested canonical Instagram identity.

    Returns:
        One best complete or partial candidate, without merging separate observations.

    Raises:
        MediaExtractionError: If no usable requested-post media exists or traversal exceeds its bound.
    """
    retained: InstagramResult | None = None
    for node in _post_nodes(documents, post.shortcode):
        try:
            candidate = _parse_post(node, post)
        except MediaExtractionError:
            continue
        retained = select_candidate(retained, candidate)
        if retained.complete:
            return retained
    if retained is None:
        msg = "Instagram browser data has no usable requested-post media."
        raise MediaExtractionError(msg)
    return retained


def _post_nodes(documents: Iterable[object], shortcode: str) -> Iterator[dict[str, object]]:
    pending: list[tuple[object, int]] = []
    visited = 0
    for document in documents:
        pending.append((document, 0))
        while pending:
            value, depth = pending.pop()
            visited += 1
            if visited > MAX_DOCUMENT_NODES or depth > MAX_DOCUMENT_DEPTH:
                msg = "Instagram structured data exceeds traversal bounds."
                raise MediaExtractionError(msg)
            node = _mapping(value)
            if _matches_post(node, shortcode):
                yield node
                continue
            pending.extend((item, depth + 1) for item in reversed(_children(value)))


def _children(value: object) -> list[object]:
    if isinstance(value, dict):
        return list(value.values())
    return _sequence(value)


def _matches_post(node: dict[str, object], shortcode: str) -> bool:
    identities = [node[key] for key in ("shortcode", "code") if key in node]
    return bool(identities) and all(value == shortcode for value in identities)


def _post_media(node: dict[str, object]) -> tuple[list[object], int | None]:
    if "edge_sidecar_to_children" in node:
        sidecar = _mapping(node["edge_sidecar_to_children"])
        edges = _sequence(sidecar.get("edges"))
        return [_mapping(edge).get("node") for edge in edges], _count(sidecar.get("count"))
    if "carousel_media" in node:
        return _sequence(node["carousel_media"]), _count(node.get("carousel_media_count"))
    media_type, typename = _media_type(node), _text(node.get("__typename"))
    if media_type == CAROUSEL_TYPE or typename in {"GraphSidecar", "XDTGraphSidecar"}:
        msg = "Instagram carousel data has no slide observations."
        raise MediaExtractionError(msg)
    single = media_type in (IMAGE_TYPE, VIDEO_TYPE) or typename in IMAGE_NAMES | VIDEO_NAMES
    return [node], 1 if single else None


def _parse_post(node: dict[str, object], post: InstagramPost) -> InstagramResult:
    media, count = _post_media(node)
    slides = _browser_slides(media, post)
    owner = _mapping(node.get("owner") or node.get("user"))
    return InstagramResult(
        post=post,
        slides=slides,
        expected_count=count,
        author_name=_text(owner.get("full_name"), "Instagram User"),
        author_handle=_text(owner.get("username"), "instagram"),
        content=_caption(node),
        video_unavailable=_missing_browser_video(media, slides),
    )


def _browser_slides(media: list[object], post: InstagramPost) -> tuple[InstagramSlide, ...]:
    slides: list[InstagramSlide] = []
    for position, value in enumerate(media[:MAX_PLAYLIST_ITEMS]):
        node = _mapping(value)
        try:
            slides.append(_browser_slide(node, post, position))
        except MediaExtractionError:
            continue
    return order_observations(slides)


def _browser_slide(node: dict[str, object], post: InstagramPost, position: int) -> InstagramSlide:
    video = _is_video(node)
    if video:
        url = _text(node.get("video_url")) or _first_url(node.get("video_versions"))
    else:
        images = _mapping(node.get("image_versions2"))
        url = _text(node.get("display_url")) or _first_url(images.get("candidates"))
    return InstagramSlide(
        identity=_text(node.get("id") or node.get("pk"), f"{post.shortcode}:{position}"),
        position=position,
        url=url,
        is_video=video,
    )


def _media_type(node: dict[str, object]) -> int | None:
    value = node.get("media_type")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_video(node: dict[str, object]) -> bool:
    return (
        node.get("is_video") is True
        or _media_type(node) == VIDEO_TYPE
        or _text(node.get("__typename")) in VIDEO_NAMES
        or bool(node.get("video_versions"))
    )


def _missing_browser_video(media: list[object], slides: tuple[InstagramSlide, ...]) -> bool:
    usable = {slide.position for slide in slides}
    return any(_is_video(_mapping(item)) and position not in usable for position, item in enumerate(media))


def _first_url(value: object) -> str:
    candidates = _sequence(value)
    return _text(_mapping(candidates[0]).get("url")) if candidates else ""


def _caption(node: dict[str, object]) -> str:
    value = node.get("caption")
    text = _text(value) or _text(_mapping(value).get("text"))
    if not text:
        caption = _mapping(node.get("edge_media_to_caption"))
        edges = _sequence(caption.get("edges"))
        if edges:
            text = _text(_mapping(_mapping(edges[0]).get("node")).get("text"))
    return clean_caption_text(text) or "Instagram Post"
