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


def test_safe_internal_identifiers_survive() -> None:
    """Job IDs, versions, permission actions and exception types remain diagnosable."""
    job_id = "0123456789abcdef0123456789abcdef"
    record = _record(
        "hovorunv2.application.jobs.feature_job_runner",
        "ticket %s version %s action %s detail %s failure %s",
        (job_id, "0.3.1", "bot:administer", "private message", KeyError),
    )
    output = PrivacyFormatter(FORMAT).format(record)
    assert job_id in output
    assert "0.3.1" in output
    assert "bot:administer" in output
    assert "KeyError" in output
    assert "private message" not in output
    assert "[omitted]" in output


def test_usage_and_access_decision_labels_survive() -> None:
    """Operational targets, buckets, units and decisions stay readable without payloads."""
    record = _record(
        "hovorunv2.application.usage.usage_administration",
        "target=%s target_id=%s bucket=%s unit=%s changed=%s detail=%s",
        ("group", -100123, "feature:youtube", "requests", True, "private caption"),
    )
    output = PrivacyFormatter(FORMAT).format(record)
    assert "target=group" in output
    assert "target_id=-100123" in output
    assert "bucket=feature:youtube" in output
    assert "unit=requests" in output
    assert "changed=True" in output
    assert "private caption" not in output
    assert "[omitted]" in output
