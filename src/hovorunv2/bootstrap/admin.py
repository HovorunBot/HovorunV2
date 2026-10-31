"""Offline administration composition root and process entry point."""

import asyncio
import sys

from dishka import AsyncContainer, Scope, alias, make_async_container, provide
from sqlalchemy.exc import SQLAlchemyError

from hovorunv2.application.access.administration import AdminRequest
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.ban_service import BanService
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.features.beta import BetaEntitlementStore
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureRegistry
from hovorunv2.application.groups.activation import ActivationStore
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.ports import (
    AuditedUnitOfWork,
    AuthorizationPolicy,
    ChatStore,
    PublicHandleStore,
    RoleStore,
    UserStore,
)
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyPublicHandleStore,
    SQLAlchemyRoleStore,
)
from hovorunv2.infrastructure.persistence.activation_store import SQLAlchemyActivationStore
from hovorunv2.infrastructure.persistence.ban_store import SQLAlchemyBanStore
from hovorunv2.infrastructure.persistence.beta_store import SQLAlchemyBetaEntitlementStore
from hovorunv2.infrastructure.persistence.chat_store import SQLAlchemyChatStore
from hovorunv2.infrastructure.persistence.group_grant_store import SQLAlchemyGroupGrantStore
from hovorunv2.infrastructure.persistence.user_store import SQLAlchemyUserStore
from hovorunv2.interface.cli.access import AccessCLI
from hovorunv2.interface.cli.roles import RoleArguments, RoleCLI, build_parser


class AdminProvider(DatabaseProvider):
    """Register only database, audit, identity lookup and role administration."""

    scope = Scope.APP

    @provide
    def get_settings(self) -> DatabaseSettings:
        """Read database configuration without requiring Telegram or encryption credentials."""
        return DatabaseSettings()

    audited_transaction_impl = provide(SQLAlchemyAuditedTransaction)
    audited_transaction = alias(source=SQLAlchemyAuditedTransaction, provides=AuditedUnitOfWork)
    role_store_impl = provide(SQLAlchemyRoleStore)
    role_store = alias(source=SQLAlchemyRoleStore, provides=RoleStore)
    handles_impl = provide(SQLAlchemyPublicHandleStore)
    handles = alias(source=SQLAlchemyPublicHandleStore, provides=PublicHandleStore)
    role_service = provide(RoleService)
    role_cli = provide(RoleCLI)
    user_store_impl = provide(SQLAlchemyUserStore)
    user_store = alias(source=SQLAlchemyUserStore, provides=UserStore)
    trust_service = provide(TrustService)
    chat_store_impl = provide(SQLAlchemyChatStore)
    chat_store = alias(source=SQLAlchemyChatStore, provides=ChatStore)
    ban_store_impl = provide(SQLAlchemyBanStore)
    ban_store = alias(source=SQLAlchemyBanStore, provides=BanStore)
    ban_service = provide(BanService)
    permission_service = provide(PermissionService)
    beta_store_impl = provide(SQLAlchemyBetaEntitlementStore)
    beta_store = alias(source=SQLAlchemyBetaEntitlementStore, provides=BetaEntitlementStore)
    beta_service = provide(BetaEligibilityService)
    activation_store_impl = provide(SQLAlchemyActivationStore)
    activation_store = alias(source=SQLAlchemyActivationStore, provides=ActivationStore)
    grants_impl = provide(SQLAlchemyGroupGrantStore)
    grants = alias(source=SQLAlchemyGroupGrantStore, provides=GroupGrantStore)
    administration_service = provide(AdministrationService)
    access_cli = provide(AccessCLI)

    @provide
    def feature_registry(self) -> FeatureRegistry:
        """Share code-defined eligibility keys without starting bot infrastructure."""
        return FEATURE_REGISTRY

    @provide
    async def authorization_policy(self) -> AuthorizationPolicy:
        """Use the same static authority policy as the running bot."""
        return await load_authorization_policy()


async def _execute_access_admin(container: AsyncContainer, arguments: RoleArguments) -> str:
    """Validate and execute access administration request."""
    request = AdminRequest.model_validate(
        {
            "action": arguments.action,
            "kind": arguments.kind,
            "target": arguments.target,
            "reason": arguments.reason,
            "public_explanation": arguments.public_explanation,
            "ban_kind": arguments.ban_kind,
            "feature": arguments.feature,
            "request_id": arguments.request_id,
        }
    )
    return await (await container.get(AccessCLI)).execute(request)


async def _execute_trust_admin(container: AsyncContainer, arguments: RoleArguments) -> str:
    """Validate target user and update trust state."""
    if arguments.target is None:
        msg = "A target user is required."
        raise ValueError(msg)
    trust = await container.get(TrustService)
    is_trusted = arguments.operation == "grant"
    found = await trust.set_trust(
        arguments.target,
        is_trusted=is_trusted,
        actor=ActorContext.cli(reason=arguments.reason),
    )
    if not found:
        msg = "No active user exists; trust administration cannot create or recover identities."
        raise ValueError(msg)
    return f"{arguments.target}\ttrusted={str(is_trusted).lower()}"


async def run_admin(arguments: RoleArguments) -> str:
    """Execute once and close all owned resources without polling or starting workers."""
    container = make_async_container(AdminProvider())
    try:
        if arguments.topic == "access":
            return await _execute_access_admin(container, arguments)
        if arguments.topic == "trust":
            return await _execute_trust_admin(container, arguments)
        cli = await container.get(RoleCLI)
        return await cli.execute(arguments)
    finally:
        await container.close()


def main() -> None:
    """Run the standalone administration command with safe operator-facing errors."""
    parser = build_parser()
    arguments = parser.parse_args(namespace=RoleArguments())
    try:
        output = asyncio.run(run_admin(arguments))
    except (ValueError, PermissionError) as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.error(
            "Database operation failed. Check DATABASE_URL and apply Alembic migrations before administration."
        )
    else:
        sys.stdout.write(output + "\n")


if __name__ == "__main__":
    main()
