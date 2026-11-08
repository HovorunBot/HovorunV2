"""Verify console and Telegram privacy with real logging and PostgreSQL authority."""

import asyncio
import io
import logging
import sys

import pytest
from aiogram import Bot
from dishka import AsyncContainer

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.jobs.http_ports import HTTPTransportError
from hovorunv2.application.media.backend_logging import MediaBackendLogger
from hovorunv2.application.media.downloader import NativeMediaDownloader
from hovorunv2.application.media.errors import MediaDownloadError
from hovorunv2.domain.enums import Role
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.logger import (
    LOG_FORMAT,
    LogContextFilter,
    PrivacyFormatter,
    reset_log_context,
    set_log_context,
    setup_telegram_logging,
)
from tests.conftest import provision_role
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.sync_http import HTTPFixture, HTTPReply
from tests.utils.telegram import API, BOT_CREDENTIAL

SENTINEL = "synthetic-private-payload-022e"
PRIVATE_URL = f"https://example.invalid/media?credential={SENTINEL}"


def payload_exception_record(name: str) -> logging.LogRecord:
    """Include private content in exception chains, arguments, cached text and stack text."""
    try:
        try:
            raise ValueError(PRIVATE_URL)  # noqa: TRY301 - exercise chained traceback privacy
        except ValueError as cause:
            raise LookupError(SENTINEL) from cause
    except LookupError:
        record = logging.LogRecord(
            name, logging.ERROR, __file__, 1, "Extraction failed for %s", (PRIVATE_URL,), sys.exc_info()
        )
    record.exc_text = SENTINEL
    record.stack_info = SENTINEL
    return record


@pytest.mark.parametrize(
    "name", ["hovorunv2.application.media.extractor", "aiohttp.client", "pydoll.browser.tab", "websockets.client"]
)
async def test_exception_payloads_never_reach_console_or_telegram(test_container: AsyncContainer, name: str) -> None:
    """Exercise both production sinks with real authority and intercepted external Telegram HTTP."""
    await provision_role(test_container, 101, Role.OWNER)
    roles = await test_container.get(RoleService)
    output = io.StringIO()
    console = logging.StreamHandler(output)
    console.setFormatter(PrivacyFormatter(LOG_FORMAT))
    console.addFilter(LogContextFilter())
    record = payload_exception_record(name)
    tokens = set_log_context(101, -101)
    async with Bot(BOT_CREDENTIAL) as bot:
        handler = setup_telegram_logging(bot, roles)
        try:
            with RecordedResponses() as http:
                endpoint = f"{API}/sendMessage"
                http.post(
                    endpoint,
                    payload={
                        "ok": True,
                        "result": {"message_id": 1, "date": 1, "chat": {"id": 101, "type": "private"}, "text": "alert"},
                    },
                )
                console.handle(record)
                handler.handle(record)
                await handler.drain()
                request = next(iter(http.requests.values()))[0]
                telegram_text = request.kwargs["data"]._fields[1][2]
            for text in (output.getvalue(), telegram_text):
                assert SENTINEL not in text
                assert PRIVATE_URL not in text
                assert "raise LookupError" not in text
                assert "LookupError" in text
                assert "payload_exception_record" in text
                assert "user=101 chat=-101" in text
            assert record.exc_text == SENTINEL
            assert record.args == (PRIVATE_URL,)
        finally:
            await handler.shutdown()
            reset_log_context(tokens)
            console.close()


async def test_media_download_failure_logs_no_message_url(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the producer emits no URL even before the sink formatter runs."""
    # Alembic migration tests disable pre-existing loggers through fileConfig.
    monkeypatch.setattr(logging.getLogger("hovorunv2.application.media.downloader"), "disabled", False)
    with (
        HTTPFixture({"/media": HTTPReply(status=500)}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as http,
    ):
        target_url = server.url(f"/media?credential={SENTINEL}")
        downloader = NativeMediaDownloader(http, destination_policy=http)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(MediaDownloadError) as error:
                await asyncio.to_thread(downloader.download, target_url)
            assert error.value.__cause__ is None
            assert error.value.__suppress_context__
        assert all(target_url not in record.getMessage() for record in caplog.records)
        assert any(record.msg == "Media download failed (%s)" for record in caplog.records), caplog.record_tuples


def test_safe_debug_metadata_survives_privacy_formatting() -> None:
    """Keep provider, status, exception type and numeric metadata useful for diagnosis."""
    record = logging.LogRecord(
        "hovorunv2.application.media.extractor",
        logging.WARNING,
        __file__,
        1,
        "%s extraction failed (%s), HTTP %d, state %s, detail %s",
        ("youtube", ValueError, 503, "approved", SENTINEL),
        None,
    )
    text = PrivacyFormatter("%(message)s").format(record)
    assert text == "youtube extraction failed (ValueError), HTTP 503, state approved, detail [omitted]"


async def test_backend_diagnostics_never_write_payloads(capsys: pytest.CaptureFixture[str]) -> None:
    """Backend retries cannot bypass logging privacy through stdout or stderr."""
    backend = MediaBackendLogger()
    backend.debug(PRIVATE_URL)
    backend.warning(PRIVATE_URL)
    backend.error(PRIVATE_URL)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_native_http_read_failure_logs_no_url(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native HTTP read failures log only failure class without request URL contents."""
    monkeypatch.setattr(logging.getLogger("hovorunv2.infrastructure.http_transport.native_http"), "disabled", False)
    with (
        HTTPFixture({"/fault": HTTPReply(status=500)}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost"})) as http,
    ):
        target_url = server.url(f"/fault?credential={SENTINEL}")
        with caplog.at_level(logging.WARNING), pytest.raises(HTTPTransportError):
            http.download_bytes(target_url)
        assert all(target_url not in record.getMessage() for record in caplog.records)
        assert all(SENTINEL not in record.getMessage() for record in caplog.records)
