"""Clamp browser callback operations to a shared extraction deadline."""

from DrissionPage.items import ChromiumElement, ChromiumTab

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserError, MediaExtractionError


def _remaining(deadline: ExtractionDeadline) -> float:
    try:
        return deadline.remaining()
    except MediaExtractionError:
        msg = "Browser extraction deadline expired."
        raise BrowserError(msg) from None


class BoundedBrowserElement:
    """Keep clicks inside the tab's original operation allowance."""

    def __init__(self, element: ChromiumElement, deadline: ExtractionDeadline) -> None:
        self._element = element
        self._deadline = deadline

    def click(self) -> object:
        """Perform a bounded SDK click without resetting the extraction deadline."""
        return self._element.click(timeout=_remaining(self._deadline))


class BoundedTabSettings:
    """Set transient cookies only while a browser extraction still owns time."""

    def __init__(self, tab: ChromiumTab, deadline: ExtractionDeadline) -> None:
        self._tab = tab
        self._deadline = deadline

    def cookies(self, cookie: dict[str, object], /) -> None:
        """Apply an explicit cookie with SDK request timeouts clamped before I/O."""
        remaining = _remaining(self._deadline)
        self._tab.set.timeouts(base=remaining, page_load=remaining, script=remaining)
        self._tab.set.cookies(cookie)


class BoundedBrowserTab:
    """Expose the application tab protocol without SDK defaults outliving a shared cutoff."""

    def __init__(self, tab: ChromiumTab, deadline: ExtractionDeadline) -> None:
        self._tab = tab
        self._deadline = deadline

    def _configure(self) -> float:
        remaining = _remaining(self._deadline)
        self._tab.set.timeouts(base=remaining, page_load=remaining, script=remaining)
        return remaining

    @property
    def html(self) -> str:
        """Read rendered HTML through a deadline-bound script operation."""
        value = self.run_js("return document.documentElement.outerHTML")
        if not isinstance(value, str):
            msg = "Browser HTML is unavailable."
            raise BrowserError(msg)
        return value

    @property
    def user_agent(self) -> str:
        """Read the current browser agent without an unbounded SDK property call."""
        value = self.run_js("return navigator.userAgent")
        if not isinstance(value, str):
            msg = "Browser user agent is unavailable."
            raise BrowserError(msg)
        return value

    @property
    def set(self) -> BoundedTabSettings:
        """Expose only deadline-bound transient settings."""
        return BoundedTabSettings(self._tab, self._deadline)

    def get(self, url: str, *, timeout: float) -> bool | None:
        """Navigate with the smaller of the requested and remaining allowance."""
        return self._tab.get(url, timeout=min(timeout, self._configure()))

    def ele(self, locator: str, *, timeout: float) -> BoundedBrowserElement | None:
        """Select an optional element without extending its caller's shared cutoff."""
        element = self._tab.ele(locator, timeout=min(timeout, self._configure()))
        if not element:
            return None
        return BoundedBrowserElement(element, self._deadline)

    def cookies(self, *, all_domains: bool = False, all_info: bool = False) -> list[dict[str, object]]:
        """Read scoped cookie facts within the same SDK allowance."""
        self._configure()
        return self._tab.cookies(all_domains=all_domains, all_info=all_info)

    def run_js(self, script: str) -> object:
        """Evaluate provider JavaScript using remaining script time only."""
        return self._tab.run_js(script, timeout=self._configure())

    def close(self) -> None:
        """Close this isolated tab; the service owns expired-browser teardown."""
        self._tab.close()
