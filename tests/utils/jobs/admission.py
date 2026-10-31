"""Admission test fixtures and service factory."""

import asyncio

from dishka import AsyncContainer

from hovorunv2.application.admission import AdmissionTicket, TicketState
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.admission_service import AdmissionService
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.usage_policy_service import UsagePolicyService
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue
from tests.utils.runtime import ManualClock

TERMINAL_WAIT_SECONDS = 15


async def admission_service(
    container: AsyncContainer, store: ValkeyAdmissionStore, queue: CeleryFeatureQueue, clock: ManualClock
) -> AdmissionService:
    """Compose the real application service and its real stores/adapters."""
    return AdmissionService(
        store,
        queue,
        await container.get(UsagePolicyService),
        await container.get(FeatureAccessService),
        await container.get(UserStore),
        await container.get(ChatStore),
        clock,
    )


async def wait_for_terminal(store: ValkeyAdmissionStore, job_id: str) -> AdmissionTicket:
    """Wait for broker execution without substituting Celery result status for admission facts."""
    async with asyncio.timeout(TERMINAL_WAIT_SECONDS):
        while True:
            ticket = await store.get(job_id)
            if ticket is None:
                msg = f"Admission ticket disappeared: {job_id}"
                raise ValueError(msg)
            if ticket.state == TicketState.TERMINAL:
                return ticket
            await asyncio.sleep(0.05)
