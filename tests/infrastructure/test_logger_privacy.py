"""Privacy formatter excludes tokens, URLs, payload arguments and exception values."""

import logging
import sys

from hovorunv2.infrastructure.logger import PrivacyFormatter, redact_sensitive_text

FORMAT = "%(message)s"


def _record(name: str, msg: object, args: object = (), exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, exc_info)


def test_redacts_bot_tokens_urls_and_bearer_values() -> None:
    """Preformatted text cannot smuggle credentials into logs."""
    text = "send https://api.telegram.org/bot123:ABC/sendMessage token bot123:ABC Bearer abc.def"
    redacted = redact_sensitive_text(text)
    assert "123:ABC" not in redacted
    assert "https://" not in redacted
    assert "abc.def" not in redacted
    assert "[url]" in redacted


def test_payload_arguments_are_omitted() -> None:
    """Arbitrary string arguments never reach the formatted record."""
    output = PrivacyFormatter(FORMAT).format(_record("hovorunv2.test", "content %s", ("private text",)))
    assert "private text" not in output
    assert "[omitted]" in output


def test_external_library_messages_are_replaced() -> None:
    """Third-party loggers cannot emit their message or arguments."""
    record = _record("aiohttp.access", "GET https://api.telegram.org/bot123:ABC/sendMessage")
    assert PrivacyFormatter(FORMAT).format(record) == "External library event"


def _raise_sensitive_error() -> None:
    msg = "private payload https://example.com/bot123:ABC"
    raise ValueError(msg)


def test_exception_values_are_omitted() -> None:
    """Exception formatting keeps only the type and frame locations."""
    try:
        _raise_sensitive_error()
    except ValueError:
        exc_info = sys.exc_info()
    record = _record("hovorunv2.test", "failure", (), exc_info)
    output = PrivacyFormatter(FORMAT).format(record)
    assert "ValueError" in output
    assert "private payload" not in output
    assert "example.com" not in output
