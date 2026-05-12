"""Browser service for tests with HTML caching and linter fixes."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from hovorunv2.infrastructure.browser import BrowserService

CACHE_DIR = Path("tests/browser_cache")
CACHE_EXPIRY_SECONDS = 7 * 24 * 60 * 60


class TestBrowserService(BrowserService):
    """Browser service for tests with persistent HTML caching."""

    def __init__(self, max_tabs: int, idle_timeout: int) -> None:
        """Initialize test browser with specific test flags."""
        super().__init__(
            max_tabs=max_tabs,
            idle_timeout=idle_timeout,
            flags=["--remote-debugging-port=0"],
        )

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch content from cache if available, else use browser."""
        await asyncio.to_thread(CACHE_DIR.mkdir, parents=True, exist_ok=True)
        url_hash = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()
        cache_file = CACHE_DIR / f"{url_hash}.json"

        if await asyncio.to_thread(cache_file.exists):
            mtime = (await asyncio.to_thread(cache_file.stat)).st_mtime
            if (time.time() - mtime) < CACHE_EXPIRY_SECONDS:

                def _read_cache() -> str:
                    with cache_file.open("r", encoding="utf-8") as f:
                        data: dict[str, Any] = json.load(f)
                        return str(data["html"])

                return await asyncio.to_thread(_read_cache)

            await asyncio.to_thread(cache_file.unlink)

        # Fallback to real browser
        html = await super().get_content(url, wait_selector)
        if html:

            def _write_cache() -> None:
                with cache_file.open("w", encoding="utf-8") as f:
                    json.dump({"url": url, "html": html}, f, ensure_ascii=False, indent=2)

            await asyncio.to_thread(_write_cache)

        return html
