"""Verify bounded account-free Google translation with synthetic loopback HTTP."""

import json
import logging
import time
from http import HTTPStatus

import pytest

from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError
from hovorunv2.application.translation.ports import CancellationToken, TranslationBudget, TranslationError
from hovorunv2.infrastructure.translation.google_engine import (
    GOOGLE_MAX_ATTEMPTS,
    HTTP_STATUS_ENHANCE_YOUR_CALM,
    GoogleTranslationEngine,
    resolve_google_language,
)
from tests.utils.sync_http import HTTPFixture, HTTPReply

pytestmark = pytest.mark.unit


class _SequenceClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ToggleCancelToken(CancellationToken):
    def __init__(self, *, cancelled_after: int = 0) -> None:
        self._cancelled_after = cancelled_after
        self._checks = 0

    def is_cancelled(self) -> bool:
        self._checks += 1
        return self._checks > self._cancelled_after


def _gtx_reply(translated_text: str) -> HTTPReply:
    body = json.dumps([[[translated_text, "src"]]]).encode()
    return HTTPReply(body=body, status=HTTPStatus.OK, headers={"Content-Type": "application/json; charset=utf-8"})


def test_google_engine_translates_unicode_and_uses_no_hidden_retries() -> None:
    """Translate Unicode text cleanly on HTTP 200 with a single request."""
    with (
        HTTPFixture({"/m": [_gtx_reply("Привіт, світе! 🌍")]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
    ):
        result = engine.translate("Hello, world! 🌍", "en", "uk")
    assert result == "Привіт, світе! 🌍"
    assert len(server.requests) == 1
    assert "sl=en" in server.requests[0].path
    assert "tl=uk" in server.requests[0].path


@pytest.mark.parametrize("transient_status", [HTTP_STATUS_ENHANCE_YOUR_CALM, int(HTTPStatus.TOO_MANY_REQUESTS)])
def test_google_engine_retries_427_and_429_with_bounded_backoff_and_jitter(
    transient_status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retry literal 427 and 429 statuses with 2s/4s exponential wait + jitter without leaking text."""
    clock = _SequenceClock(100.0)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    replies = [
        HTTPReply(body=b"rate limited", status=transient_status),
        HTTPReply(body=b"rate limited", status=transient_status),
        _gtx_reply("Добрий день"),
    ]
    with (
        caplog.at_level(logging.WARNING),
        HTTPFixture({"/m": replies}) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"),
            allowed_hosts=frozenset({"127.0.0.1"}),
            clock_fn=clock,
            sleep_fn=fake_sleep,
            jitter_fn=lambda upper: upper * 0.5,
        ) as engine,
    ):
        result = engine.translate("Bonjour", "fr", "uk")

    assert result == "Добрий день"
    assert len(server.requests) == GOOGLE_MAX_ATTEMPTS
    assert sleeps == [2.25, 4.25]
    assert "Bonjour" not in caplog.text
    assert "Добрий день" not in caplog.text
    assert "/m?" not in caplog.text


def test_google_engine_honors_retry_after_within_budget_and_aborts_beyond_budget() -> None:
    """Honor Retry-After when it fits the remaining budget and abort when it exceeds it."""
    clock = _SequenceClock(100.0)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    with (
        HTTPFixture(
            {
                "/m": [
                    HTTPReply(body=b"slow down", status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "3"}),
                    _gtx_reply("Готово"),
                ]
            }
        ) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"),
            allowed_hosts=frozenset({"127.0.0.1"}),
            clock_fn=clock,
            sleep_fn=fake_sleep,
            jitter_fn=lambda _upper: 0.0,
        ) as engine,
    ):
        budget = TranslationBudget.from_timeout(15.0, now_monotonic=clock())
        assert engine.translate_with_budget("Ready", "en", "uk", budget=budget) == "Готово"
    assert sleeps == [3.0]

    sleeps.clear()
    with (
        HTTPFixture(
            {
                "/m": [
                    HTTPReply(body=b"slow down", status=HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "12"}),
                    _gtx_reply("Should not be called"),
                ]
            }
        ) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"),
            allowed_hosts=frozenset({"127.0.0.1"}),
            clock_fn=clock,
            sleep_fn=fake_sleep,
            jitter_fn=lambda _upper: 0.0,
        ) as engine,
    ):
        budget = TranslationBudget.from_timeout(10.0, now_monotonic=clock())
        with pytest.raises(TranslationError, match="Retry-After"):
            engine.translate_with_budget("Ready", "en", "uk", budget=budget)
    assert len(server.requests) == 1
    assert sleeps == []


def test_google_engine_exhausts_three_attempts_and_aborts_when_wait_exceeds_budget() -> None:
    """Stop after 3 attempts on persistent 429 and abort early if upcoming wait exceeds deadline."""
    clock = _SequenceClock(100.0)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    with (
        HTTPFixture(
            {
                "/m": [
                    HTTPReply(body=b"busy", status=HTTPStatus.TOO_MANY_REQUESTS),
                    HTTPReply(body=b"busy", status=HTTPStatus.TOO_MANY_REQUESTS),
                    HTTPReply(body=b"busy", status=HTTPStatus.TOO_MANY_REQUESTS),
                ]
            }
        ) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"),
            allowed_hosts=frozenset({"127.0.0.1"}),
            clock_fn=clock,
            sleep_fn=fake_sleep,
            jitter_fn=lambda _upper: 0.0,
        ) as engine,
        pytest.raises(TranslationError, match="429"),
    ):
        engine.translate("Hello", "en", "uk")
    assert len(server.requests) == GOOGLE_MAX_ATTEMPTS
    assert sleeps == [2.0, 4.0]

    sleeps.clear()
    with (
        HTTPFixture({"/m": [HTTPReply(body=b"busy", status=HTTPStatus.TOO_MANY_REQUESTS)]}) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"),
            allowed_hosts=frozenset({"127.0.0.1"}),
            clock_fn=clock,
            sleep_fn=fake_sleep,
            jitter_fn=lambda _upper: 0.0,
        ) as engine,
    ):
        tight_budget = TranslationBudget.from_timeout(1.5, now_monotonic=clock())
        with pytest.raises(TranslationError, match="budget"):
            engine.translate_with_budget("Hello", "en", "uk", budget=tight_budget)
    assert len(server.requests) == 1
    assert sleeps == []


def test_google_engine_rejects_non_retryable_invalid_malformed_oversized_slow_and_cancelled() -> None:
    """Fail fast on 403, invalid languages, malformed/oversized replies, slow reads, and cancellation."""
    with (
        HTTPFixture({"/m": [HTTPReply(body=b"forbidden", status=HTTPStatus.FORBIDDEN)]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
    ):
        with pytest.raises(ValueError, match="Unsupported"):
            engine.translate("Hello", "und", "uk")
        assert len(server.requests) == 0

        with pytest.raises(TranslationError, match="403"):
            engine.translate("Hello", "en", "uk")
        assert len(server.requests) == 1

    with (
        HTTPFixture({"/m": [HTTPReply(body=b"{}")]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
        pytest.raises(TranslationError, match="missing result container"),
    ):
        engine.translate("Hello", "en", "uk")

    with (
        HTTPFixture({"/m": [_gtx_reply("A" * 2048)]}) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"}), max_response_bytes=128
        ) as engine,
        pytest.raises(TranslationError, match="size limit"),
    ):
        engine.translate("Hello", "en", "uk")

    with (
        HTTPFixture({"/m": [HTTPReply(body=b"too slow", delay_seconds=0.5)]}) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"),
            allowed_hosts=frozenset({"127.0.0.1"}),
            max_attempts=1,
            total_timeout_seconds=1.0,
            connect_timeout_seconds=0.1,
            read_timeout_seconds=0.1,
        ) as engine,
        pytest.raises(TranslationError, match="transport failure"),
    ):
        engine.translate("Hello", "en", "uk")

    with (
        HTTPFixture({"/m": [_gtx_reply("Should not run")]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
    ):
        budget = TranslationBudget.from_timeout(15.0)
        with pytest.raises(TranslationError, match="cancelled"):
            engine.translate_with_budget(
                "Hello",
                "en",
                "uk",
                budget=budget,
                cancel_token=_ToggleCancelToken(cancelled_after=0),
            )
        assert len(server.requests) == 0


def test_google_engine_translates_long_document_and_discards_partial_on_late_failure() -> None:
    """Translate multi-segment documents in order and discard all partial output if a late segment fails."""
    first_para = "Sentence one. " * 250
    second_para = "Final anchor sentence."
    long_doc = f"{first_para.strip()}\n\n{second_para}"

    with (
        HTTPFixture(
            {
                "/m": [
                    _gtx_reply("Перша частина."),
                    _gtx_reply("Фінальне речення."),
                ]
            }
        ) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
    ):
        result = engine.translate(long_doc, "en", "uk")
    assert result.splitlines() == ["Перша частина.", "", "Фінальне речення."]
    assert len(server.requests) == 2

    with (
        HTTPFixture(
            {
                "/m": [
                    _gtx_reply("Перша частина."),
                    HTTPReply(body=b"forbidden", status=HTTPStatus.FORBIDDEN),
                ]
            }
        ) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
        pytest.raises(TranslationError, match="403"),
    ):
        engine.translate(long_doc, "en", "uk")
    assert len(server.requests) == 2


def test_google_hard_deadline_stops_drip_fed_response() -> None:
    """Stop a slow continuous response within the shared allowance rather than resetting read timeouts."""
    chunks = (b'[[["', *(b"word " for _ in range(20)), b'", "src"]]]')
    with (
        HTTPFixture({"/m": [HTTPReply(chunks=chunks, chunk_delay_seconds=0.05)]}) as server,
        GoogleTranslationEngine(
            base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"}), max_attempts=1
        ) as engine,
    ):
        started = time.monotonic()
        with pytest.raises(TranslationError):
            engine.translate("Bonjour", "fr", "uk", budget=TranslationBudget.from_timeout(0.15))
        assert time.monotonic() - started < 0.8
        assert len(server.requests) == 1


def test_google_gtx_json_endpoint_translates_segments() -> None:
    """When configured with a GTX URL, GoogleTranslationEngine sends client=gtx and parses JSON fragments."""
    payload = (
        b'[[["\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd1\x96\xd1\x82. ", "Hola. "],'
        b' ["\xd0\xa1\xd0\xb2\xd1\x96\xd1\x82.", "Mundo."]]]'
    )
    with (
        HTTPFixture({"/translate_a/single": [HTTPReply(body=payload, status=HTTPStatus.OK)]}) as server,
        GoogleTranslationEngine(
            base_url=server.url("/translate_a/single"), allowed_hosts=frozenset({"127.0.0.1"})
        ) as engine,
    ):
        translated = engine.translate("Hola. Mundo.", "es", "uk")
    assert translated == "Привіт. Світ."
    assert len(server.requests) == 1
    path = server.requests[0].path
    assert "client=gtx" in path
    assert "dt=t" in path
    assert "sl=es" in path
    assert "tl=uk" in path
    assert "q=" in path


def test_google_engine_computes_positive_read_timeout_when_remaining_below_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When remaining <= connect_timeout_seconds, read_timeout must remain min(read_timeout, remaining)."""
    captured_timeouts: list[object] = []

    class _RecordingSession:
        def get(self, url: str, **kwargs: object) -> object:
            captured_timeouts.append(kwargs.get("timeout"))
            msg = "stop after capturing timeout"
            raise TranslationError(msg)

        def close(self) -> None:
            return None

    clock = _SequenceClock(100.0)
    with GoogleTranslationEngine(
        base_url="https://synthetic.invalid/m",
        allowed_hosts=frozenset({"synthetic.invalid"}),
        connect_timeout_seconds=5.0,
        read_timeout_seconds=10.0,
        clock_fn=clock,
    ) as engine:
        monkeypatch.setattr(engine, "_session", _RecordingSession())
        budget = TranslationBudget.from_timeout(3.0, now_monotonic=clock())
        with pytest.raises(TranslationError, match="stop after capturing timeout"):
            engine.translate_with_budget("Hello world", "en", "uk", budget=budget)

    assert captured_timeouts == [(3.0, 3.0)]


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1:8080/m",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/m",
        "http://[::1]/m",
        "http://user:pass@example.com/m",
    ],
)
def test_google_engine_rejects_ssrf_base_url(unsafe_url: str) -> None:
    """Custom TRANSLATION_ONLINE_URL values targeting private, loopback, or non-HTTP origins are rejected."""
    with pytest.raises(HTTPTransportError) as error:
        GoogleTranslationEngine(base_url=unsafe_url)
    assert error.value.code == HTTPFailure.REJECTED


def test_google_engine_rejects_html_fallback_on_gtx_endpoint() -> None:
    """GTX endpoint requires valid JSON and rejects HTML payloads without BeautifulSoup fallback."""
    html_reply = HTTPReply(
        body=b'<html><body><div class="result-container">Hello</div></body></html>',
        status=HTTPStatus.OK,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    with (
        HTTPFixture({"/m": [html_reply]}) as server,
        GoogleTranslationEngine(base_url=server.url("/m"), allowed_hosts=frozenset({"127.0.0.1"})) as engine,
        pytest.raises(TranslationError, match="invalid JSON"),
    ):
        engine.translate("Hello", "en", "uk")


def test_google_engine_resolves_static_google_language_codes() -> None:
    """Static Google language table normalizes aliases, regional codes, and undetermined tags."""
    assert resolve_google_language("en") == "en"
    assert resolve_google_language("uk") == "uk"
    assert resolve_google_language("zh-CN") == "zh-CN"
    assert resolve_google_language("zh-TW") == "zh-TW"
    assert resolve_google_language("he") == "iw"
    assert resolve_google_language("jv") == "jw"
    assert resolve_google_language("und") is None
