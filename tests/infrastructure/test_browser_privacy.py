"""Prevent browser SDK frames and cookie values from escaping through diagnostic sinks."""

import logging
import sys

import pytest

from hovorunv2.infrastructure.logger import PrivacyFormatter

pytestmark = pytest.mark.unit
COOKIE = "browser-session-123"
CONTENT = "quiet forest caption"
URL = "https://example.invalid/post?session=browser-session-123"


@pytest.mark.parametrize("name", ["pydoll", "pydoll.browser.tab", "websockets", "websockets.client"])
@pytest.mark.parametrize(
    ("message", "arguments"),
    [
        ("Navigating to URL: %s", (URL,)),
        ("Sending command: %s", ({"method": "Network.setCookies", "params": {"value": COOKIE}},)),
        (f"Received frame {COOKIE} content {CONTENT}", ()),
        ("Received frame %s", (CONTENT,)),
        ("Headers: %s", ({"Cookie": f"sessionid={COOKIE}"},)),
    ],
)
def test_browser_diagnostics_never_expose_frames_or_cookies(
    name: str, message: str, arguments: tuple[object, ...]
) -> None:
    """Protect both preformatted data and argument containers from SDK log producers."""
    record = logging.LogRecord(name, logging.INFO, __file__, 1, message, arguments, None)
    output = PrivacyFormatter("%(name)s %(levelname)s %(message)s").format(record)
    assert COOKIE not in output
    assert CONTENT not in output
    assert URL not in output
    assert name in output
    assert record.msg == message


@pytest.mark.parametrize("name", ["pydoll.connection", "websockets.client"])
def test_browser_exception_values_cannot_leak_through_another_handler(name: str) -> None:
    """Keep exception type and code location while excluding arbitrary browser exception text."""
    message = f"Rejected frame {COOKIE} {CONTENT}"
    try:
        raise ConnectionError(message)  # noqa: TRY301 - exercise exception output.
    except ConnectionError:
        record = logging.LogRecord(name, logging.ERROR, __file__, 1, "Transport failure", (), sys.exc_info())
    record.exc_text = COOKIE
    record.stack_info = CONTENT
    for _ in range(2):
        output = PrivacyFormatter("%(message)s").format(record)
        assert COOKIE not in output
        assert CONTENT not in output
        assert "ConnectionError" in output
        assert "test_browser_exception_values" in output
    assert record.exc_text == COOKIE
