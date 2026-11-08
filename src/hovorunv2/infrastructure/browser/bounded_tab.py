"""Adapt synchronous CDP operations to the application browser protocol and deadlines."""

import math
from collections.abc import Generator
from contextlib import contextmanager

lazy from pydoll.protocol.network.types import CookieParam
lazy from pydoll.protocol.runtime.methods import EvaluateResponse
lazy from pydoll.sync import Tab, WebElement

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserError, MediaExtractionError
from hovorunv2.infrastructure.browser.operation_timeout import BrowserOperationTimeout

DEFAULT_OPERATION_SECONDS = 30
MILLISECONDS_PER_SECOND = 1000


def _remaining(deadline: ExtractionDeadline) -> float:
    try:
        return deadline.remaining()
    except MediaExtractionError:
        msg = "Browser extraction deadline expired."
        raise BrowserError(msg) from None


def _operation_deadline(
    deadline: ExtractionDeadline | None, maximum: float = DEFAULT_OPERATION_SECONDS
) -> ExtractionDeadline:
    if deadline is None:
        return ExtractionDeadline.after(maximum)
    _remaining(deadline)
    return deadline.narrow(maximum)


@contextmanager
def _operation(deadline: ExtractionDeadline) -> Generator[None]:
    try:
        with BrowserOperationTimeout(deadline):
            yield
    except TimeoutError:
        msg = "Browser operation timed out."
        raise BrowserError(msg) from None


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = ', "\'", '.join(f"'{part}'" for part in value.split("'"))
    return f"concat({parts})"


def _cookie_param(cookie: dict[str, object]) -> CookieParam:
    name, value = cookie.get("name"), cookie.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        msg = "Browser cookies require string names and values."
        raise TypeError(msg)
    result = CookieParam(name=name, value=value)
    domain, path, url = cookie.get("domain"), cookie.get("path"), cookie.get("url")
    if isinstance(domain, str):
        result["domain"] = domain
    if isinstance(path, str):
        result["path"] = path
    if isinstance(url, str):
        result["url"] = url
    secure, http_only = cookie.get("secure"), cookie.get("httpOnly")
    if isinstance(secure, bool):
        result["secure"] = secure
    if isinstance(http_only, bool):
        result["httpOnly"] = http_only
    return result


class BoundedBrowserElement:
    """Keep clicks inside the owning extraction's allowance."""

    def __init__(self, element: WebElement, deadline: ExtractionDeadline | None) -> None:
        self._element = element
        self._deadline = deadline

    def click(self) -> object:
        """Perform a synchronous SDK click bounded by the shared cutoff."""
        with _operation(_operation_deadline(self._deadline)):
            return self._element.click()


class BoundedTabSettings:
    """Set transient cookies within the tab's isolated browser context."""

    def __init__(self, tab: Tab, deadline: ExtractionDeadline | None) -> None:
        self._tab = tab
        self._deadline = deadline

    def cookies(self, cookie: dict[str, object], /) -> None:
        """Convert one application cookie and bound the SDK request."""
        parameter = _cookie_param(cookie)
        with _operation(_operation_deadline(self._deadline)):
            self._tab.set_cookies([parameter])


class BoundedBrowserTab:
    """Expose SDK-neutral synchronous operations without extending shared deadlines."""

    def __init__(self, tab: Tab, deadline: ExtractionDeadline | None) -> None:
        self._tab = tab
        self._deadline = deadline

    @property
    def html(self) -> str:
        """Read rendered HTML through a deadline-bound evaluation."""
        value = self.run_js("return document.documentElement.outerHTML")
        if not isinstance(value, str):
            msg = "Browser HTML is unavailable."
            raise BrowserError(msg)
        return value

    @property
    def user_agent(self) -> str:
        """Read the current browser agent through bounded evaluation."""
        value = self.run_js("return navigator.userAgent")
        if not isinstance(value, str):
            msg = "Browser user agent is unavailable."
            raise BrowserError(msg)
        return value

    @property
    def set(self) -> BoundedTabSettings:
        """Expose bounded transient cookie settings."""
        return BoundedTabSettings(self._tab, self._deadline)

    def get(self, url: str, *, timeout: float) -> bool:
        """Navigate within the smaller of the requested and shared allowance."""
        operation = _operation_deadline(self._deadline, timeout)
        with _operation(operation):
            # The SDK annotates whole seconds; the alarm enforces the exact fractional cutoff.
            self._tab.go_to(url, timeout=math.ceil(_remaining(operation)))
        return True

    def ele(self, locator: str, *, timeout: float) -> BoundedBrowserElement | None:
        """Select optional CSS or substring text matches within a bounded lookup."""
        expression = locator
        if locator.startswith("text="):
            expression = f"//*[contains(text(), {_xpath_literal(locator.removeprefix('text='))})]"
        operation = _operation_deadline(self._deadline, timeout)
        try:
            with _operation(operation):
                element = self._tab.query(expression, timeout=math.ceil(_remaining(operation)), raise_exc=False)
        except BrowserError:
            if self._deadline is not None:
                _remaining(self._deadline)
            return None
        if element is None:
            return None
        return BoundedBrowserElement(element, self._deadline)

    def cookies(self, *, all_domains: bool = False, all_info: bool = False) -> list[dict[str, object]]:
        """Read cookie dictionaries from this extraction's isolated browser context."""
        with _operation(_operation_deadline(self._deadline)):
            return [dict(cookie) for cookie in self._tab.get_cookies()]

    def run_js(self, script: str) -> object:
        """Return an evaluated Python value or raise a safe error for script failure.

        Args:
            script: JavaScript expression or statements with a top-level return.

        Returns:
            The unwrapped CDP value.

        Raises:
            BrowserError: If evaluation fails or its deadline expires.
        """
        operation = _operation_deadline(self._deadline)
        with _operation(operation):
            response = self._evaluate(script, operation)
            failure = response["result"].get("exceptionDetails", {}).get("exception", {})
            if failure.get("description") == "SyntaxError: Illegal return statement":
                # An explicit return before the first brace bypasses the SDK's return-detection heuristic.
                response = self._evaluate(f"return (\n(function() {{\n{script}\n}})()\n)", operation)
        if "exceptionDetails" in response["result"]:
            msg = "Browser script evaluation failed."
            raise BrowserError(msg)
        return response["result"]["result"].get("value")

    def _evaluate(self, script: str, operation: ExtractionDeadline) -> EvaluateResponse:
        return self._tab.execute_script(
            script,
            return_by_value=True,
            await_promise=True,
            user_gesture=True,
            timeout=_remaining(operation) * MILLISECONDS_PER_SECOND,
        )

    def close(self) -> None:
        """Close the tab within a fresh cleanup allowance, independent of expired work."""
        with _operation(ExtractionDeadline.after(DEFAULT_OPERATION_SECONDS)):
            self._tab.close()
