"""Pure provider configuration shared by asynchronous ingress and native worker extraction."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from hovorunv2.domain.enums import CommandName


@dataclass(frozen=True)
class OGConfig:
    """Declarative configuration for OpenGraph metadata extraction."""

    default_author: str
    author_handle: str
    author_url: str
    title_separator: str | None = None
    title_suffix: str | None = None
    headers: Mapping[str, str] | None = None
    video_url_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class BrowserFallbackConfig:
    """Declarative configuration for headless browser fallback extraction."""

    wait_selector: str
    gate_buttons: tuple[str, ...] = ()
    js_extractor: str | None = None
    default_author: str = "User"
    author_handle: str = "user"
    author_url: str = ""
    default_title: str = "Post"
    default_footer: str = ""
    description_clean_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class ProviderConfig:
    """Declarative configuration for a media provider."""

    name: CommandName
    pattern: re.Pattern[str]
    platform_name: str = ""
    redirect_pattern: re.Pattern[str] | None = None
    post_url_pattern: re.Pattern[str] | None = None
    strip_hashtags: bool = False
    merge_quoted_media: bool = True
    merge_quoted_only_when_empty: bool = False
    translate_quoted: bool = True
    headers: Mapping[str, str] = field(default_factory=dict)
    og_config: OGConfig | None = None
    browser_config: BrowserFallbackConfig | None = None

    @property
    def label(self) -> str:
        """Human-readable platform name."""
        return self.platform_name or str(self.name).capitalize()


FACEBOOK_CONFIG = ProviderConfig(
    name=CommandName.FACEBOOK,
    pattern=re.compile(
        r"https?://(?:[\w-]+\.)?facebook\.com/(?:[^/]+/posts/\d+|groups/[^/]+/posts/\d+|"
        r"permalink\.php|photo\.php|reel/[\w-]+|watch/|videos/[\w-]+|share/[vpr]/[\w-]+|story\.php|(?P<id>\w+))"
    ),
    og_config=OGConfig(
        default_author="Facebook User",
        author_handle="facebook",
        author_url="https://www.facebook.com",
        title_separator=" - ",
        headers={"User-Agent": "facebookexternalhit/1.1"},
        video_url_pattern=re.compile(r"facebook\.com/(?:reel/|watch/|videos/|share/[vr]/|[^/]+/videos/)"),
    ),
)
