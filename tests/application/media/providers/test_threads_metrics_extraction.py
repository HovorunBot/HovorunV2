"""Tests for Threads metrics extraction logic."""

import aiohttp

from hovorunv2.application.media.providers.threads import ThreadsService
from hovorunv2.infrastructure.browser import BrowserService


async def test_extract_footer_from_soup() -> None:
    """Test extracting footer statistics from Threads nested JSON payload."""
    browser_service = BrowserService(max_tabs=1, idle_timeout=1)

    post_id = "DX9HWZiiE2Q"
    url = f"https://www.threads.net/@user/post/{post_id}"
    html_content = f"""
    <html>
    <head>
        <meta property="og:title" content="User (@user) on Threads"/>
        <meta property="og:description" content="Post content"/>
    </head>
    <body>
        <article>
            <div dir="auto">Post content</div>
        </article>
        <script type="application/json">
        {{
            "require": [],
            "data": {{
                "media": {{
                    "code": "{post_id}",
                    "like_count": 2864,
                    "text_post_app_info": {{
                        "direct_reply_count": 77,
                        "repost_count": 20
                    }}
                }}
            }}
        }}
        </script>
    </body>
    </html>
    """
    async with aiohttp.ClientSession() as http:
        service = ThreadsService(http, browser_service)
        payload = service._parse_browser_post(html_content, url, "net", post_id)  # noqa: SLF001

    assert payload is not None
    assert "💬 77" in payload.footer_text
    assert "🔄 20" in payload.footer_text
    assert "❤️ 2.9K" in payload.footer_text  # 2864 -> 2.9K


async def test_extract_payload_with_metrics() -> None:
    """Test full payload extraction including metrics for Threads."""
    # Mock dependencies
    browser_service = BrowserService(max_tabs=1, idle_timeout=1)

    post_id = "DX9HWZiiE2Q"
    url = f"https://www.threads.net/@user/post/{post_id}"

    html_content = f"""
    <html>
    <head>
        <meta property="og:title" content="User (@user) on Threads"/>
        <meta property="og:description" content="Post content"/>
        <meta property="og:image" content="https://example.com/img.jpg"/>
    </head>
    <body>
        <article>
            <div dir="auto">Post content</div>
        </article>
        <script type="application/json">
        {{
            "code": "{post_id}",
            "like_count": 1234,
            "direct_reply_count": 56,
            "repost_count": 7
        }}
        </script>
    </body>
    </html>
    """

    async with aiohttp.ClientSession() as http:
        service = ThreadsService(http, browser_service)
        payload = service._parse_browser_post(html_content, url, "net", post_id)  # noqa: SLF001

    assert payload is not None
    assert "💬 56" in payload.footer_text
    assert "🔄 7" in payload.footer_text
    assert "❤️ 1.2K" in payload.footer_text
