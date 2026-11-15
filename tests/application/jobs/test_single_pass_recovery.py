"""Behavioral tests for single-pass recovery, role parity, and publisher reuse."""

import time
from collections.abc import Mapping
from pathlib import Path

from dishka import AsyncContainer

from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.jobs.admission import TicketState
from hovorunv2.application.jobs.feature_job_runner import TIMEOUT_NOTICE_TEXT
from hovorunv2.application.jobs.feature_jobs import JobOutcome, JobTarget
from hovorunv2.application.jobs.http_ports import HTTPFiles, HTTPForm, HTTPHeaders, HTTPQuery
from hovorunv2.bootstrap.worker_runtime import WorkerChildContext, worker_child_context
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName, TargetKind, WorkerRole
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from tests.utils.jobs import admit_test_job, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import usage_subjects


class _RecordingSyncHTTP:
    """Minimal SyncHTTPPort recording Telegram Bot API calls."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, HTTPForm | None]] = []

    def destination_allowed(self, url: str) -> bool:
        del url
        return True

    def validate_url(self, url: str) -> None:
        del url

    def read_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        del url, params, headers
        return {}

    def read_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str:
        del url, params, headers
        return ""

    def resolve(self, url: str, headers: HTTPHeaders | None = None) -> str:
        return url

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        del url, headers, cookies
        return b""

    def download_to_path(
        self,
        url: str,
        dest: Path,
        headers: HTTPHeaders | None = None,
        *,
        cookies: Mapping[str, str] | None = None,
    ) -> Path:
        del url, headers, cookies
        dest.write_bytes(b"")
        return dest

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: HTTPForm | None = None,
        files: HTTPFiles | None = None,
    ) -> object:
        del files
        self.requests.append((method, url, data))
        return {"ok": True, "result": {"message_id": 100 + len(self.requests)}}


async def test_operations_worker_recover_notifies_expired_and_clears_disabled(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Operations worker recovery sends timeout notices, clears disabled features, and reuses publisher."""
    user, _ = await usage_subjects(test_container)
    target = JobTarget(
        kind=TargetKind.USER,
        entity_id=user.entity_id,
        generation=user.generation,
        telegram_id=user.telegram_id,
    )
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    emergency = await test_container.get(FeatureEmergencyService)
    await emergency.set_state(
        CommandName.TWITTER.value, disabled=True, reason="synthetic outage", actor=ActorContext.cli()
    )
    http = _RecordingSyncHTTP()
    async with runtime_cache(test_container) as (cache, namespace):
        expired_env = job_envelope(
            "a" * 32,
            clock,
            job_id="ops-expired-job",
            feature=CommandName.INSTAGRAM.value,
            target=target,
            actor_id=user.telegram_id,
            actor_generation=user.generation,
            charged_telegram_id=user.telegram_id,
            dedupe_key="a" * 64,
            deadline=clock.now() + 10,
        )
        disabled_env = job_envelope(
            "b" * 32,
            clock,
            job_id="ops-disabled-job",
            feature=CommandName.TWITTER.value,
            target=target,
            actor_id=user.telegram_id,
            actor_generation=user.generation,
            charged_telegram_id=user.telegram_id,
            dedupe_key="b" * 64,
            deadline=clock.now() + 120,
        )
        await admit_test_job(settings, cache, namespace, clock, expired_env)
        await admit_test_job(settings, cache, namespace, clock, disabled_env)
        clock.value += 20

        context = WorkerChildContext(
            settings=settings,
            namespace=namespace,
            clock=clock,
            role=WorkerRole.OPERATIONS,
            http=http,
        )
        context.open()
        try:
            publisher_before = context._publisher  # noqa: SLF001
            assert publisher_before is not None
            recovered = context.recover()
            assert recovered >= 2
            assert context._publisher is publisher_before  # noqa: SLF001
            assert context.recover() >= 0
            assert context._publisher is publisher_before  # noqa: SLF001
        finally:
            context.close()

        store = ValkeyAdmissionStore(cache, namespace)
        expired_ticket = await store.get(expired_env.job_id)
        assert expired_ticket is not None
        assert expired_ticket.state == TicketState.TERMINAL
        assert expired_ticket.outcome == JobOutcome.EXPIRED

        disabled_ticket = await store.get(disabled_env.job_id)
        assert disabled_ticket is not None
        assert disabled_ticket.state == TicketState.TERMINAL
        assert disabled_ticket.outcome == JobOutcome.CANCELLED

        sent_texts = [data.get("text") for _, url, data in http.requests if url.endswith("/sendMessage") and data]
        assert TIMEOUT_NOTICE_TEXT in sent_texts


async def test_feature_runner_recover_does_not_double_rotate_same_lane(
    test_container: AsyncContainer, postgres_database_url: str
) -> None:
    """Single-pass recovery notifies and settles the same ticket without double-rotating its lane."""
    user, _ = await usage_subjects(test_container)
    target = JobTarget(
        kind=TargetKind.USER,
        entity_id=user.entity_id,
        generation=user.generation,
        telegram_id=user.telegram_id,
    )
    settings = (await test_container.get(Settings)).model_copy(update={"database_url": postgres_database_url})
    clock = ManualClock(time.time())
    http = _RecordingSyncHTTP()
    async with runtime_cache(test_container) as (cache, namespace):
        first = job_envelope(
            "c" * 32,
            clock,
            job_id="lane-job-1",
            feature=CommandName.INSTAGRAM.value,
            target=target,
            actor_id=user.telegram_id,
            actor_generation=user.generation,
            charged_telegram_id=user.telegram_id,
            dedupe_key="c" * 64,
            message_id=101,
            deadline=clock.now() + 10,
        )
        second = job_envelope(
            "d" * 32,
            clock,
            job_id="lane-job-2",
            feature=CommandName.INSTAGRAM.value,
            target=target,
            actor_id=user.telegram_id,
            actor_generation=user.generation,
            charged_telegram_id=user.telegram_id,
            dedupe_key="d" * 64,
            message_id=102,
            deadline=clock.now() + 10,
        )
        await admit_test_job(settings, cache, namespace, clock, first)
        await admit_test_job(settings, cache, namespace, clock, second)
        clock.value += 20

        with worker_child_context(settings, namespace=namespace, clock=clock, http=http) as runner:
            runner.recover()

        store = ValkeyAdmissionStore(cache, namespace)
        first_ticket = await store.get(first.job_id)
        second_ticket = await store.get(second.job_id)
        assert first_ticket is not None
        assert second_ticket is not None

        # Single rotation processes only the head ticket of the single lane:
        # first_ticket must be BOTH notified AND settled to TERMINAL(EXPIRED),
        # while second_ticket remains ACCEPTED until the next sweep.
        assert first_ticket.state == TicketState.TERMINAL
        assert first_ticket.outcome == JobOutcome.EXPIRED
        assert second_ticket.state == TicketState.ACCEPTED

        sent_messages = [data for _, url, data in http.requests if url.endswith("/sendMessage") and data]
        assert len(sent_messages) == 1
        assert sent_messages[0].get("text") == TIMEOUT_NOTICE_TEXT
        assert sent_messages[0].get("reply_to_message_id") == 101
