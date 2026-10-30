"""Admission test fixtures and service factory."""

from dishka import AsyncContainer

from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.admission_service import AdmissionService
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.usage_policy_service import UsagePolicyService
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue
from tests.utils.runtime import ManualClock


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
