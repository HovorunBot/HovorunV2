"""Bounded cleanup of work for durably disabled features in the native worker sweep."""

import time

from dishka import AsyncContainer

from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.jobs.admission import TicketState
from hovorunv2.application.jobs.feature_jobs import JobOutcome
from hovorunv2.bootstrap.worker_runtime import worker_child_context
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from tests.utils.jobs import admit_test_job, job_envelope
from tests.utils.runtime import ManualClock, runtime_cache
from tests.utils.usage import usage_subjects

PROVIDER = CommandName.TWITTER
OTHER = CommandName.INSTAGRAM


async def test_disabled_feature_sweep_cancels_pending_work_and_payloads(
    test_container: AsyncContainer,
) -> None:
    """A disabled provider's pending work is cancelled and its ciphertext removed once."""
    await usage_subjects(test_container)
    settings = await test_container.get(Settings)
    clock = ManualClock(time.time())
    emergency = await test_container.get(FeatureEmergencyService)
    await emergency.set_state(PROVIDER.value, disabled=True, reason="synthetic outage", actor=ActorContext.cli())
    async with runtime_cache(test_container) as (cache, namespace):
        disabled = job_envelope("a" * 32, clock, job_id="disabled-job", feature=PROVIDER.value, dedupe_key="a" * 64)
        other = job_envelope("b" * 32, clock, job_id="other-job", feature=OTHER.value, dedupe_key="b" * 64)
        await admit_test_job(settings, cache, namespace, clock, disabled)
        await admit_test_job(settings, cache, namespace, clock, other)
        store = ValkeyAdmissionStore(cache, namespace)
        with worker_child_context(settings, namespace=namespace, clock=clock) as runner:
            assert runner.recover() >= 1
            assert runner.recover() >= 0
        disabled_ticket = await store.get(disabled.job_id)
        assert disabled_ticket is not None
        assert disabled_ticket.state == TicketState.TERMINAL
        assert disabled_ticket.outcome == JobOutcome.CANCELLED
        other_ticket = await store.get(other.job_id)
        assert other_ticket is not None
        assert other_ticket.state == TicketState.ACCEPTED
        assert await cache.get(namespace.value + "payload:" + disabled.payload_ref) is None
        assert await cache.get(namespace.value + "payload:" + other.payload_ref) is not None
