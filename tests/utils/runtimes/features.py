"""Synthetic feature access evaluator test runtime and definitions."""

from dishka import AsyncContainer

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.features import FeatureDefinition, FeatureRegistry
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.policy_facts import FeatureFactStore
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from tests.utils.runtimes.beta import REGISTRY, group_beta_runtime

CAPABLE = FeatureDefinition(
    "capability_feature", "Capability feature", required_bot_capabilities=frozenset({"can_pin_messages"})
)


async def feature_runtime(container: AsyncContainer, registry: FeatureRegistry = REGISTRY) -> FeatureAccessService:
    """Compose the production evaluator with real stores and a test-only feature registry."""
    await group_beta_runtime(container)
    service = FeatureAccessService(
        registry,
        await container.get(UserStore),
        await container.get(ChatStore),
        await container.get(BanStore),
        await container.get(MembershipStore),
        await container.get(PermissionService),
        await container.get(ChatStatusService),
        await container.get(GroupBetaStore),
        await container.get(FeatureFactStore),
    )
    access = await container.get(AccessService)
    access.features = service
    return service
