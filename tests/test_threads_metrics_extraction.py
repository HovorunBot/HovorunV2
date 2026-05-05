
import pytest
import html
import re
from unittest.mock import AsyncMock, MagicMock
from bs4 import BeautifulSoup
from hovorunv2.application.clients.threads import ThreadsService

@pytest.mark.asyncio
async def test_extract_footer_from_soup():
    # Mock dependencies
    translation_service = MagicMock()
    browser_service = MagicMock()
    service = ThreadsService(translation_service, browser_service)
    
    post_id = "DX9HWZiiE2Q"
    html_content = f"""
    <html>
    <body>
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
    soup = BeautifulSoup(html_content, "html.parser")
    
    footer = service._extract_footer_from_soup(soup, post_id)
    
    assert "💬 77" in footer
    assert "🔄 20" in footer
    assert "❤️ 2.9K" in footer  # 2864 -> 2.9K

@pytest.mark.asyncio
async def test_extract_payload_with_metrics():
    # Mock dependencies
    translation_service = MagicMock()
    translation_service.translate_if_needed = AsyncMock(return_value=None)
    browser_service = MagicMock()
    
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
    
    browser_service.get_content = AsyncMock(return_value=html_content)
    service = ThreadsService(translation_service, browser_service)
    
    session = MagicMock()
    payload = await service.extract_payload(session, url, 123, "telegram")
    
    assert payload is not None
    assert "💬 56" in payload.footer_text
    assert "🔄 7" in payload.footer_text
    assert "❤️ 1.2K" in payload.footer_text
