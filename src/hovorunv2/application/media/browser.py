"""Typed browser callback operations without an infrastructure or SDK dependency."""

from collections.abc import Callable
from typing import Protocol

from hovorunv2.application.media.errors import MediaFailure


class BrowserElement(Protocol):
    """A selected element's optional gate interaction."""

    def click(self) -> object:
        """Perform one click, with the SDK result left to its owner."""
        ...


class BrowserTabSettings(Protocol):
    """Transient cookie configuration before navigation."""

    def cookies(self, cookie: dict[str, object], /) -> None:
        """Set one cookie inside the owning browser session."""
        ...


class BrowserTab(Protocol):
    """Only browser operations needed by application extraction callbacks."""

    @property
    def html(self) -> str:
        """Return current rendered HTML."""
        ...

    @property
    def user_agent(self) -> str:
        """Return the browser's HTTP user agent."""
        ...

    @property
    def set(self) -> BrowserTabSettings:
        """Expose transient tab settings."""
        ...

    def get(self, url: str, *, timeout: float) -> bool | None:
        """Navigate once with a bounded timeout."""
        ...

    def ele(self, locator: str, *, timeout: float) -> BrowserElement | None:
        """Select an optional element within a bounded timeout."""
        ...

    def cookies(self, *, all_domains: bool = False, all_info: bool = False) -> list[dict[str, object]]:
        """Read transient cookie metadata for scoped transfer."""
        ...

    def run_js(self, script: str) -> object:
        """Return untrusted provider metadata from JavaScript."""
        ...

    def close(self) -> None:
        """Close this isolated tab."""
        ...


class BrowserPort(Protocol):
    """Protocol for headless browser automation."""

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Return rendered HTML; navigation or browser failures raise."""
        ...

    async def extract_and_download[T](
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], T | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[T | None, list[bytes | MediaFailure]]:
        """Run extraction and download inside one browser session.

        Returns (None, []) when extraction finds no metadata. Each media
        position carries either bytes or an explicit MediaFailure.

        Args:
            url: URL to open in the browser.
            extractor_fn: Callback that extracts a value from the open tab.
            wait_selector: Optional CSS selector to await before extracting.
            cookies: Optional cookies to inject before navigation.

        Returns:
            The extracted value paired with one media outcome per position.
        """
        ...
