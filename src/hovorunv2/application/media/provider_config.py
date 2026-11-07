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


INSTAGRAM_JS_EXTRACTOR: str = """
() => {
    const getMeta = (prop) => {
        const el = document.querySelector(`meta[property="${prop}"], meta[name="${prop}"]`);
        return el ? el.content : null;
    };

    let data = {
        video: getMeta('og:video') || getMeta('og:video:secure_url'),
        image: getMeta('og:image'),
        title: getMeta('og:title') || document.title,
        description: getMeta('og:description') || "",
        likes: null,
        views: null,
        comments: null
    };

    const findData = (obj) => {
        if (obj && obj.items && obj.items[0]) return obj.items[0];
        if (obj && obj.graphql && obj.graphql.shortcode_media) return obj.graphql.shortcode_media;
        return null;
    };

    const post = findData(window.__additionalDataLoaded && window.__additionalDataLoaded.feed) ||
                 findData(window._sharedData && window._sharedData.entry_data &&
                          window._sharedData.entry_data.PostPage &&
                          window._sharedData.entry_data.PostPage[0]);

    if (post) {
        data.title = data.title || (post.caption ? (post.caption.text || post.caption) : "");
        data.video = data.video || (post.video_url ||
                    (post.video_versions ? post.video_versions[0].url : null));
        data.image = data.image || (post.display_url ||
                    (post.image_versions2 ? post.image_versions2.candidates[0].url : null));
        data.likes = data.likes || (post.like_count ||
                    (post.edge_media_preview_like ? post.edge_media_preview_like.count : null));
        data.comments = data.comments || (post.comment_count ||
                    (post.edge_media_to_comment ? post.edge_media_to_comment.count : null));
        data.views = data.views || (post.video_view_count || post.view_count || null);
    }
    return data;
}
"""

TWITTER_CONFIG = ProviderConfig(
    name=CommandName.TWITTER,
    pattern=re.compile(
        r"https?://(?:www\.)?(?:api\.)?(?:x\.com|twitter\.com)/(?:\w+/status/|2/tweets/)(?P<post_id>\d+)"
    ),
)

INSTAGRAM_CONFIG = ProviderConfig(
    name=CommandName.INSTAGRAM,
    strip_hashtags=True,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )
    },
    pattern=re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:(?P<username>[\w.-]+)/)?(?P<type>p|reel|reels|tv)/(?P<id>[\w-]+)"
    ),
    browser_config=BrowserFallbackConfig(
        wait_selector='article, main, [role="main"]',
        gate_buttons=(
            "Allow all cookies",
            "Accept All",
            "Allow essential and optional cookies",
            "Decline optional cookies",
        ),
        js_extractor=INSTAGRAM_JS_EXTRACTOR,
        default_author="Instagram User",
        author_handle="instagram",
        author_url="https://www.instagram.com/",
        default_title="Instagram Post",
        description_clean_pattern=re.compile(r"^\d+ likes?, \d+ comments? - [^:]+: "),
    ),
    og_config=OGConfig(
        default_author="Instagram User",
        author_handle="instagram",
        author_url="https://www.instagram.com/",
        title_separator=" on Instagram: ",
    ),
)

TIKTOK_CONFIG = ProviderConfig(
    name=CommandName.TIKTOK,
    strip_hashtags=True,
    pattern=re.compile(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/(?:(?P<type>v|t|video|photo)/|(?P<username>[@\w.-]+)/)?"
        r"(?:(?P<type_alt>video|photo)/)?(?P<id>[\w-]+)/?(?:(?<=/)\w+/?)*(?=[^\w/]|$)"
    ),
)

THREADS_CONFIG = ProviderConfig(
    name=CommandName.THREADS,
    merge_quoted_media=False,
    translate_quoted=False,
    pattern=re.compile(r"https?://(?:www\.)?threads\.(?P<tld>net|com)/(?:@[\w.-]+/)?(?:post|t)/(?P<post_id>[\w-]+)"),
    og_config=OGConfig(
        default_author="Threads User",
        author_handle="threads",
        author_url="https://www.threads.net",
        title_suffix=" on Threads",
    ),
)

BLUESKY_CONFIG = ProviderConfig(
    name=CommandName.BLUESKY,
    redirect_pattern=re.compile(r"go\.bsky\.app"),
    pattern=re.compile(r"https?://(?:www\.|go\.)?bsky\.app/(?:profile/(?P<handle>[\w.-]+)/post/)?(?P<rkey>[\w.-]+)"),
)

_YOUTUBE_OG_CONFIG = OGConfig(
    default_author="YouTube Channel",
    author_handle="youtube",
    author_url="https://www.youtube.com",
    title_suffix=" - YouTube",
)

YOUTUBE_CONFIG = ProviderConfig(
    name=CommandName.YOUTUBE,
    platform_name="YouTube",
    pattern=re.compile(
        r"https?://(?:(?:www|m)\.)?(?:"
        r"youtube\.com/(?:"
        r"shorts/(?P<short_id>[\w-]+)|"
        r"watch\?(?:[^\s#]*&)?v=(?P<video_id>[\w-]+)|"
        r"(?:(?:channel|c|user)/[\w-]+/|@[\w.-]+/)?post/(?P<post_id>[\w-]+)|"
        r"(?:(?:channel|c|user)/[\w-]+|@[\w.-]+)/community\?(?:[^\s#]*&)?lb=(?P<community_id>[\w-]+)"
        r")|"
        r"youtu\.be/(?P<youtu_be_id>[\w-]+)"
        r")"
    ),
    post_url_pattern=re.compile(
        r"https?://(?:(?:www|m)\.)?youtube\.com/(?:"
        r"(?:(?:channel|c|user)/[\w-]+/|@[\w.-]+/)?post/(?P<post_id>[\w-]+)|"
        r"(?:(?:channel|c|user)/[\w-]+|@[\w.-]+)/community\?(?:[^\s#]*&)?lb=(?P<community_id>[\w-]+)"
        r")"
    ),
    headers={
        "Cookie": "SOCS=CAESEwgDEgk2MTQ1NzU1MDQaAmVuIAEaBgiA_LyaBg",
    },
    og_config=_YOUTUBE_OG_CONFIG,
)

REDDIT_CONFIG = ProviderConfig(
    name=CommandName.REDDIT,
    headers={
        "User-Agent": "TelegramBot (like TwitterBot)",
    },
    pattern=re.compile(
        r"https?://(?:"
        r"(?:[\w-]+\.)?reddit\.com/(?:r|u|user)/[\w-]+/(?:comments/(?P<post_id>[\w-]+)(?:/[^\s/?#]*)?/?|s/(?P<share_id>[\w-]+))|"
        r"redd\.it/(?P<short_id>[\w-]+)"
        r")"
    ),
)

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

DEFAULT_PROVIDER_CONFIGS: tuple[ProviderConfig, ...] = (
    TWITTER_CONFIG,
    INSTAGRAM_CONFIG,
    TIKTOK_CONFIG,
    THREADS_CONFIG,
    BLUESKY_CONFIG,
    FACEBOOK_CONFIG,
    YOUTUBE_CONFIG,
    REDDIT_CONFIG,
)
