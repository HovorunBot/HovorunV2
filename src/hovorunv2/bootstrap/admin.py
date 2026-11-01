"""Offline administration composition root and process entry point."""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

from dishka import AsyncContainer, Scope, alias, make_async_container, provide
from sqlalchemy.exc import SQLAlchemyError

from hovorunv2.application.access.administration import AdminRequest
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.ban_service import BanService
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.ports import AuthorizationPolicy, RoleStore
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.beta import BetaEntitlementStore
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.emergency import FeatureEmergencyStore
from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureRegistry
from hovorunv2.application.groups.activation import ActivationStore
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.lifecycle import IdentityLifecycleService
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.identity.recovery import RecoveryOption, RecoveryPreviewService, RecoveryStore
from hovorunv2.application.operations.audit_inspection import AuditInspectionService, AuditQueryStore
from hovorunv2.application.usage.budgets import Clock, WallClock
from hovorunv2.application.usage.policies import UsagePolicyStore, UsageSafetyCaps
from hovorunv2.application.usage.usage_administration import UsageAdministrationService
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.infrastructure.logger import setup_logging
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyPublicHandleStore,
    SQLAlchemyRoleStore,
)
from hovorunv2.infrastructure.persistence.stores.activation_store import SQLAlchemyActivationStore
from hovorunv2.infrastructure.persistence.stores.audit_query_store import SQLAlchemyAuditQueryStore
from hovorunv2.infrastructure.persistence.stores.ban_store import SQLAlchemyBanStore
from hovorunv2.infrastructure.persistence.stores.beta_store import SQLAlchemyBetaEntitlementStore
from hovorunv2.infrastructure.persistence.stores.chat_store import SQLAlchemyChatStore
from hovorunv2.infrastructure.persistence.stores.feature_emergency_store import SQLAlchemyFeatureEmergencyStore
from hovorunv2.infrastructure.persistence.stores.group_grant_store import SQLAlchemyGroupGrantStore
from hovorunv2.infrastructure.persistence.stores.recovery_store import SQLAlchemyRecoveryStore
from hovorunv2.infrastructure.persistence.stores.usage_policy_store import SQLAlchemyUsagePolicyStore
from hovorunv2.infrastructure.persistence.stores.user_store import SQLAlchemyUserStore
from hovorunv2.interface.cli.access import AccessCLI
from hovorunv2.interface.cli.roles import RoleArguments, RoleCLI, build_parser

logger = logging.getLogger(__name__)


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
    lifecycle_service = provide(IdentityLifecycleService)
    clock_impl = provide(WallClock)
    clock = alias(source=WallClock, provides=Clock)
    recovery_store_impl = provide(SQLAlchemyRecoveryStore)
    recovery_store = alias(source=SQLAlchemyRecoveryStore, provides=RecoveryStore)
    recovery_service = provide(RecoveryPreviewService)
    audit_store_impl = provide(SQLAlchemyAuditQueryStore)
    audit_store = alias(source=SQLAlchemyAuditQueryStore, provides=AuditQueryStore)
    audit_inspection = provide(AuditInspectionService)
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
    usage_policy_store_impl = provide(SQLAlchemyUsagePolicyStore)
    usage_policy_store = alias(source=SQLAlchemyUsagePolicyStore, provides=UsagePolicyStore)

    @provide
    def usage_safety_caps(self) -> UsageSafetyCaps:
        """Provide application ceilings separately from ordinary quota overrides."""
        return UsageSafetyCaps()

    usage_policy_service = provide(UsagePolicyService)
    usage_administration_service = provide(UsageAdministrationService)
    feature_emergency_store_impl = provide(SQLAlchemyFeatureEmergencyStore)
    feature_emergency_store = alias(source=SQLAlchemyFeatureEmergencyStore, provides=FeatureEmergencyStore)
    feature_emergency_service = provide(FeatureEmergencyService)
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
    logger.info("Admin access command executing: action=%s kind=%s", arguments.action, arguments.kind)
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
            "bucket": arguments.bucket,
            "unit": arguments.unit,
            "window_seconds": arguments.window_seconds,
            "limit": arguments.limit,
            "burst": arguments.burst,
            "concurrency": arguments.concurrency,
            "pending": arguments.pending,
        }
    )
    return await (await container.get(AccessCLI)).execute(request)


async def _execute_feature_admin(container: AsyncContainer, arguments: RoleArguments) -> str:
    """Apply one CLI-origin feature emergency transition and report its durable state."""
    logger.info("Admin feature command executing: feature=%s operation=%s", arguments.feature, arguments.operation)
    service = await container.get(FeatureEmergencyService)
    state = await service.set_state(
        arguments.feature,
        disabled=arguments.operation == "disable",
        reason=arguments.reason or "",
        public_reason=arguments.public_explanation,
        actor=ActorContext.cli(reason=arguments.reason),
    )
    public_reason = state.public_reason or "-"
    return (
        f"{state.feature_key}\t{'disabled' if state.disabled else 'enabled'}"
        f"\tepoch={state.epoch}\tpublic_reason={public_reason}"
    )


async def _execute_audit_admin(container: AsyncContainer, arguments: RoleArguments) -> str:
    """Return one bounded audit page for CLI inspection."""
    logger.info("Admin audit query executing: limit=%d", arguments.limit or 20)
    service = await container.get(AuditInspectionService)
    page = await service.query(
        actor=ActorContext.cli(),
        target_type=arguments.entity,
        action=arguments.audit_action,
        since=_parse_timestamp(arguments.since),
        until=_parse_timestamp(arguments.until),
        before=arguments.before,
        limit=arguments.limit or 20,
    )
    lines = [
        f"{entry.id}\t{entry.created_at.isoformat()}\t{entry.target_type}\t{entry.target_id}\t"
        f"{entry.action}\t{entry.actor_origin.value}\t{entry.actor_id or '-'}\t"
        f"{json.dumps(entry.fields, ensure_ascii=True, default=str)}\t{entry.reason or '-'}"
        for entry in page.entries
    ]
    if page.next_cursor:
        lines.append(f"next_cursor\t{page.next_cursor}")
    return "\n".join(lines) or "No audit entries."


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def _execute_lifecycle_admin(container: AsyncContainer, arguments: RoleArguments) -> str:
    """Apply one CLI-authorized identity soft-deletion and report the new generation."""
    logger.info("Admin lifecycle command executing: operation=%s", arguments.operation)
    if arguments.target is None or arguments.reason is None:
        msg = "Lifecycle deletion requires --target and --reason."
        raise ValueError(msg)
    if not arguments.yes:
        msg = "Lifecycle deletion requires --yes."
        raise ValueError(msg)
    service = await container.get(IdentityLifecycleService)
    actor = ActorContext.cli(reason=arguments.reason)
    if arguments.operation == "delete-user":
        return await service.delete_user(arguments.target, reason=arguments.reason, actor=actor)
    if arguments.operation == "delete-group":
        return await service.delete_group(arguments.target, reason=arguments.reason, actor=actor)
    if arguments.operation in {"restore-user", "fresh-user", "restore-group", "fresh-group"}:
        if arguments.revision is None:
            msg = "Recovery requires --revision from the preview."
            raise ValueError(msg)
        recovery = await container.get(RecoveryPreviewService)
        option = RecoveryOption.RESTORE if arguments.operation.startswith("restore") else RecoveryOption.FRESH
        if arguments.operation.endswith("user"):
            result = await recovery.execute_user(int(arguments.target), option, arguments.revision, actor=actor)
        else:
            result = await recovery.execute_group(int(arguments.target), option, arguments.revision, actor=actor)
        return f"{result.telegram_id}\t{result.option.value}\tgeneration={result.generation}\tbanned={result.banned}"
    msg = "Unknown lifecycle operation."
    raise ValueError(msg)


async def _execute_trust_admin(container: AsyncContainer, arguments: RoleArguments) -> str:
    """Validate target user and update trust state."""
    logger.info("Admin trust command executing: operation=%s", arguments.operation)
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
    logger.info("Admin command started (topic: %s)", arguments.topic)
    container = make_async_container(AdminProvider())
    try:
        if arguments.topic == "access":
            result = await _execute_access_admin(container, arguments)
        elif arguments.topic == "trust":
            result = await _execute_trust_admin(container, arguments)
        elif arguments.topic == "feature":
            result = await _execute_feature_admin(container, arguments)
        elif arguments.topic == "lifecycle":
            result = await _execute_lifecycle_admin(container, arguments)
        elif arguments.topic == "audit":
            result = await _execute_audit_admin(container, arguments)
        else:
            cli = await container.get(RoleCLI)
            result = await cli.execute(arguments)
        logger.info("Admin command completed successfully (topic: %s)", arguments.topic)
        return result
    finally:
        await container.close()


def main() -> None:
    """Run the standalone administration command with safe operator-facing errors."""
    setup_logging()
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
