"""Validate ordered Instagram candidates and select complete or retained partial media."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from ipaddress import ip_address
from urllib.parse import urlsplit

from hovorunv2.application.media.errors import MediaExtractionError
from hovorunv2.application.media.limits import MAX_PLAYLIST_ITEMS
from hovorunv2.application.media.payloads import MediaItem, RichMediaPayload

POST_PATH = re.compile(
    r"/(?:[\w.-]+/)?(?P<kind>p|reel|reels|tv)/(?P<code>[A-Za-z0-9_-]+)(?:/c/[A-Za-z0-9_-]+(?:/r/[A-Za-z0-9_-]+)?)?/?"
)
INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
INCOMPLETE_MEDIA_TEXT = "⚠️ Some media may be missing."


@dataclass
class InstagramPayload(RichMediaPayload):
    """Preserve provider completeness through browser downloads without changing shared DTOs."""

    complete: bool = False


def require_public_media_url(value: object) -> str:
    """Require a structurally public HTTPS target before transport-level DNS checks.

    Args:
        value: Untrusted media URL.

    Returns:
        Validated HTTPS URL.

    Raises:
        MediaExtractionError: If the URL is malformed or identifies a local target.
    """
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        msg = "Instagram media target is invalid."
        raise MediaExtractionError(msg)
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        valid_origin = parts.scheme == "https" and bool(host) and parts.port in {None, 443}
    except ValueError:
        msg = "Instagram media target is invalid."
        raise MediaExtractionError(msg) from None
    if not valid_origin or parts.username is not None or parts.password is not None:
        msg = "Instagram media target is invalid."
        raise MediaExtractionError(msg)
    _require_public_host(host)
    return value


def _require_public_host(host: str) -> None:
    try:
        address = ip_address(host)
    except ValueError:
        normalized = host.rstrip(".").lower()
        if "." not in normalized or normalized.endswith((".localhost", ".local", ".internal")):
            msg = "Instagram media target is not public."
            raise MediaExtractionError(msg) from None
    else:
        if not address.is_global:
            msg = "Instagram media target is not public."
            raise MediaExtractionError(msg)


@dataclass(frozen=True)
class InstagramPost:
    """Identify a requested public post without retaining tracking parameters."""

    shortcode: str = field(repr=False)
    kind: str

    def __post_init__(self) -> None:
        """Reject unsupported identity components before constructing a canonical link."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.shortcode) or self.kind not in {"p", "reel", "reels", "tv"}:
            msg = "Instagram post identity is invalid."
            raise MediaExtractionError(msg)

    @classmethod
    def from_url(cls, url: str) -> InstagramPost:
        """Validate a supported Instagram link and discard tracking data.

        Args:
            url: Original public post link.

        Returns:
            Canonical post identity.

        Raises:
            MediaExtractionError: If the URL does not identify a supported public post.
        """
        try:
            parts = urlsplit(url)
            valid_origin = parts.scheme in {"http", "https"} and parts.hostname in INSTAGRAM_HOSTS
            valid_port = parts.port in {None, 80, 443}
        except ValueError:
            msg = "Instagram post identity is invalid."
            raise MediaExtractionError(msg) from None
        if not valid_origin or not valid_port:
            msg = "Instagram post identity is invalid."
            raise MediaExtractionError(msg)
        if parts.username is not None or parts.password is not None:
            msg = "Instagram post identity is invalid."
            raise MediaExtractionError(msg)
        match = POST_PATH.fullmatch(parts.path)
        if match is None:
            msg = "Instagram post identity is invalid."
            raise MediaExtractionError(msg)
        return cls(match["code"], match["kind"])

    @property
    def canonical_url(self) -> str:
        """Return the original provider's HTTPS URL without tracking parameters."""
        return f"https://www.instagram.com/{self.kind}/{self.shortcode}/"


@dataclass(frozen=True)
class InstagramSlide:
    """Keep a slide's position and identity independent of its signed media URL."""

    identity: str = field(repr=False)
    position: int
    url: str = field(repr=False)
    is_video: bool

    def __post_init__(self) -> None:
        """Require a stable slide identity, nonnegative position and safe URL shape."""
        if not self.identity:
            msg = "Instagram slide identity is required."
            raise MediaExtractionError(msg)
        if isinstance(self.position, bool) or self.position < 0:
            msg = "Instagram slide position is invalid."
            raise MediaExtractionError(msg)
        require_public_media_url(self.url)


def order_observations(observations: Iterable[InstagramSlide]) -> tuple[InstagramSlide, ...]:
    """Deduplicate repeated slide observations without collapsing equal media URLs.

    Args:
        observations: Slide observations carrying stable identity and original position.

    Returns:
        Unique slides in their original position order, retaining the first URL observation.

    Raises:
        MediaExtractionError: If a position conflicts or unique media exceed the item bound.
    """
    positions: dict[int, InstagramSlide] = {}
    for slide in observations:
        previous = positions.get(slide.position)
        if previous is not None:
            if previous.identity != slide.identity or previous.is_video != slide.is_video:
                msg = "Instagram slide observations conflict."
                raise MediaExtractionError(msg)
            continue
        if len(positions) >= MAX_PLAYLIST_ITEMS:
            msg = "Instagram slide observations exceed the item bound."
            raise MediaExtractionError(msg)
        positions[slide.position] = slide
    return tuple(positions[position] for position in sorted(positions))


@dataclass(frozen=True)
class InstagramResult:
    """Retain usable ordered media and explicit evidence of album completeness."""

    post: InstagramPost = field(repr=False)
    slides: tuple[InstagramSlide, ...] = field(repr=False)
    expected_count: int | None = None
    terminal_verified: bool = False
    author_name: str = field(default="Instagram User", repr=False)
    author_handle: str = field(default="instagram", repr=False)
    content: str = field(default="Instagram Post", repr=False)
    video_unavailable: bool = False

    def __post_init__(self) -> None:
        """Establish bounded nonempty media and consistent count/position invariants."""
        if not self.slides or len(self.slides) > MAX_PLAYLIST_ITEMS:
            msg = "Instagram extraction requires bounded usable media."
            raise MediaExtractionError(msg)
        if self.expected_count is not None:
            self._validate_count(self.expected_count)
        positions = tuple(slide.position for slide in self.slides)
        if positions != tuple(sorted(set(positions))):
            msg = "Instagram slides must have unique increasing positions."
            raise MediaExtractionError(msg)

    def _validate_count(self, count: int) -> None:
        if isinstance(count, bool) or count < len(self.slides):
            msg = "Instagram media count is inconsistent."
            raise MediaExtractionError(msg)
        if self.slides[-1].position >= count:
            msg = "Instagram slide exceeds the media count."
            raise MediaExtractionError(msg)

    @property
    def complete(self) -> bool:
        """Require contiguous usable positions plus count agreement or a verified terminal."""
        if tuple(slide.position for slide in self.slides) != tuple(range(len(self.slides))):
            return False
        if self.expected_count is not None:
            return len(self.slides) == self.expected_count
        return self.terminal_verified

    def to_payload(self) -> InstagramPayload:
        """Map one candidate to the existing payload without combining extraction attempts.

        Returns:
            Ordered media with canonical provider link and an honest partial indication.
        """
        return InstagramPayload(
            author_name=self.author_name,
            author_handle=self.author_handle,
            author_url="https://www.instagram.com/",
            content=self.content,
            footer_text="" if self.complete else INCOMPLETE_MEDIA_TEXT,
            original_url=self.post.canonical_url,
            media_items=[MediaItem(slide.url, slide.is_video) for slide in self.slides],
            video_unavailable=self.video_unavailable,
            complete=self.complete,
        )


def select_candidate(previous: InstagramResult | None, candidate: InstagramResult) -> InstagramResult:
    """Prefer a complete attempt, then the largest partial, retaining earlier ties.

    Args:
        previous: Earlier retained candidate, if one exists.
        candidate: Later usable attempt for the same requested post.

    Returns:
        One complete or best retained partial attempt, preserving its item order.

    Raises:
        MediaExtractionError: If attempts identify different posts.
    """
    if previous is None:
        return candidate
    if previous.post != candidate.post:
        msg = "Instagram attempts must identify the same post."
        raise MediaExtractionError(msg)
    if previous.complete:
        return previous
    if candidate.complete or len(candidate.slides) > len(previous.slides):
        return candidate
    return previous
