"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, alias, provide

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.ban_service import BanService
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.features.beta import BetaEntitlementStore
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.group_beta import GroupBetaStore
from hovorunv2.application.features.group_beta_service import GroupBetaService
from hovorunv2.application.features.policy_facts import FeatureFactStore
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureRegistry
from hovorunv2.application.ports import (
    AuthorizationPolicy,
    RoleStore,
)
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyRoleStore,
)
from hovorunv2.infrastructure.persistence.stores.ban_store import SQLAlchemyBanStore
from hovorunv2.infrastructure.persistence.stores.beta_store import SQLAlchemyBetaEntitlementStore
from hovorunv2.infrastructure.persistence.stores.feature_fact_store import SQLAlchemyFeatureFactStore
from hovorunv2.infrastructure.persistence.stores.group_beta_store import SQLAlchemyGroupBetaStore
from hovorunv2.interface.telegram.handlers.base import BaseCommand

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


class SecurityAndAccessProvider(Provider):
    """Provide security policies, roles, bans, access control and beta features."""

    scope = Scope.APP

    @provide
    async def authorization_policy(self) -> AuthorizationPolicy:
        """Load static permission assets once without changing live enforcement paths."""
        return await load_authorization_policy()

    @provide
    def feature_registry(self) -> FeatureRegistry:
        """Provide the code-defined registry shared by eligibility and feature evaluation."""
        return FEATURE_REGISTRY

    role_store_impl = provide(SQLAlchemyRoleStore)
    role_store = alias(source=SQLAlchemyRoleStore, provides=RoleStore)
    role_service = provide(RoleService)

    ban_store_impl = provide(SQLAlchemyBanStore)
    ban_store = alias(source=SQLAlchemyBanStore, provides=BanStore)
    ban_service = provide(BanService)

    trust_service = provide(TrustService)
    permission_service = provide(PermissionService)
    access_service = provide(AccessService)

    beta_store_impl = provide(SQLAlchemyBetaEntitlementStore)
    beta_store = alias(source=SQLAlchemyBetaEntitlementStore, provides=BetaEntitlementStore)
    beta_service = provide(BetaEligibilityService)

    group_beta_store_impl = provide(SQLAlchemyGroupBetaStore)
    group_beta_store = alias(source=SQLAlchemyGroupBetaStore, provides=GroupBetaStore)
    group_beta_service = provide(GroupBetaService)

    feature_fact_store = provide(SQLAlchemyFeatureFactStore, provides=FeatureFactStore)
    feature_access = provide(FeatureAccessService)
