"""Native worker owner alerts use real authority and loopback Telegram HTTP."""

import asyncio
import io
import logging
from urllib.parse import parse_qs

import pytest
from celery.signals import after_setup_logger, after_setup_task_logger
from celery.utils.dispatch import Signal
from dishka import AsyncContainer

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.bootstrap.worker_logging import HARD_TIMEOUT_TEMPLATE, WorkerTelegramHandler
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Role
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import PrivacyFormatter
from hovorunv2.utils.job_logging import current_job_id, reset_job_log_context, set_job_log_context
from tests.conftest import provision_role
from tests.utils.sync_http import HTTPFixture, json_reply

OWNER = 950101
SENTINEL = "synthetic-private-worker-input"
JOB_ID = "a" * 32


def _emit_error(handler: WorkerTelegramHandler, name: str, message: str, args: tuple[object, ...]) -> None:
    record = logging.LogRecord(name, logging.ERROR, __file__, 1, message, args, None)
    handler.handle(record)


@pytest.mark.parametrize("name", ["celery.worker.request", "hovorunv2.application.media.extractor"])
async def test_worker_alerts_resolve_current_owners_and_omit_private_arguments(
    test_container: AsyncContainer, name: str
) -> None:
    """Parent timeout and child errors route synchronously without retaining a pool across calls."""
    await provision_role(test_container, OWNER, Role.OWNER)
    settings = await test_container.get(Settings)
    endpoint = "/bot" + settings.bot_token + "/sendMessage"
    with HTTPFixture({endpoint: json_reply({"ok": True, "result": {"message_id": 1}})}) as server:
        handler = WorkerTelegramHandler(settings, api_base=server.url(""))
        try:
            message = HARD_TIMEOUT_TEMPLATE if name == "celery.worker.request" else "Media failed for %s"
            args = (120, SENTINEL, JOB_ID) if name == "celery.worker.request" else (SENTINEL,)
            tokens = set_job_log_context(JOB_ID, "media")
            try:
                await asyncio.to_thread(_emit_error, handler, name, message, args)
            finally:
                reset_job_log_context(tokens)
            assert current_job_id.get() == "-"
            assert len(server.requests) == 1
            fields = parse_qs(server.requests[0].body.decode())
            assert fields["chat_id"] == [str(OWNER)]
            assert SENTINEL not in fields["text"][0]
            assert "Worker Log Alert" in fields["text"][0]
            assert "job=" + JOB_ID in fields["text"][0]
            if name == "celery.worker.request":
                assert "hard time limit exceeded" in fields["text"][0]
            roles = await test_container.get(RoleService)
            await roles.set_role(OWNER, Role.USER, actor=ActorContext.cli())
            await asyncio.to_thread(_emit_error, handler, name, message, args)
            assert len(server.requests) == 1
        finally:
            handler.close()


@pytest.mark.unit
@pytest.mark.parametrize("signal", [after_setup_logger, after_setup_task_logger])
@pytest.mark.parametrize("enabled", [False, True])
def test_worker_logging_signals_install_private_sinks_and_optional_owner_alerts(
    monkeypatch: pytest.MonkeyPatch, signal: Signal, *, enabled: bool
) -> None:
    """Both parent and task logging honor the routing switch without requiring a running event loop."""
    monkeypatch.setenv("ERROR_DM_ENABLED", str(enabled))
    monkeypatch.setenv("BOT_TOKEN", "123456:synthetic-logging-token")
    worker_logger = logging.getLogger("synthetic-worker")
    previous_handlers = worker_logger.handlers.copy()
    output = io.StringIO()
    console = logging.StreamHandler(output)
    worker_logger.handlers = [console]
    try:
        signal.send(sender=None, logger=worker_logger)
        signal.send(sender=None, logger=worker_logger)
        assert isinstance(console.formatter, PrivacyFormatter)
        assert sum(isinstance(handler, WorkerTelegramHandler) for handler in worker_logger.handlers) == int(enabled)
    finally:
        for handler in worker_logger.handlers:
            handler.close()
        worker_logger.handlers = previous_handlers
