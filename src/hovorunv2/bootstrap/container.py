"""Dependency injection configuration using dishka."""

from collections.abc import AsyncIterable
from typing import NewType

import aiohttp
from aiogram import Bot
from dishka import Provider, Scope, alias, provide

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.ban_service import BanService
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.features.beta import BetaEntitlementStore
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.group_beta import GroupBetaStore
from hovorunv2.application.features.group_beta_service import GroupBetaService
from hovorunv2.application.features.policy_facts import FeatureFactStore
from hovorunv2.application.features.registry import FEATURE_REGISTRY, FeatureRegistry
from hovorunv2.application.groups.activation import ActivationStore
from hovorunv2.application.groups.activation_service import GroupActivationService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.groups.group_migration import GroupMigrationStore
from hovorunv2.application.identity.identity import GroupIdentityStore
from hovorunv2.application.identity.membership import MembershipPort
from hovorunv2.application.identity.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.jobs.admission import AdmissionPort
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_job_runner import default_media_clients
from hovorunv2.application.jobs.feature_jobs import FeatureQueuePort, PayloadPort
from hovorunv2.application.media.providers.base import BaseMediaClient
from hovorunv2.application.messaging.feedback_service import FeedbackService
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.operations.cleanup_service import CleanupService
from hovorunv2.application.operations.notification_service import NotificationService
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.application.ports import (
    AppSettings,
    AuditedUnitOfWork,
    AuthorizationPolicy,
    CachePort,
    ChatStore,
    CommandStore,
    PublicHandleStore,
    RoleStore,
    SystemStore,
    UnauthorizedPromptSender,
    UserStore,
)
from hovorunv2.application.settings.admin_session_service import AdminSessionService
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.application.translation.language_service import LanguageService
from hovorunv2.application.translation.providers import (
    BaseTranslationService,
    GoogleTranslationService,
)
from hovorunv2.application.translation.service import TranslationService
from hovorunv2.application.usage.budget_service import BudgetService
from hovorunv2.application.usage.budgets import BudgetPort, Clock, RuntimeNamespace, WallClock
from hovorunv2.application.usage.policies import UsagePolicyStore, UsageSafetyCaps
from hovorunv2.application.usage.usage_ledger import UsageLedgerStore
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.budgets import ValkeyBudgetStore
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import DatabaseSettings, Settings
from hovorunv2.infrastructure.feature_payloads import ValkeyPayloadStore
from hovorunv2.infrastructure.feature_queue import CeleryFeatureQueue
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyChatStore,
    SQLAlchemyCommandStore,
    SQLAlchemyPublicHandleStore,
    SQLAlchemyRoleStore,
    SQLAlchemySystemStore,
    SQLAlchemyUserStore,
)
from hovorunv2.infrastructure.persistence.stores.activation_store import SQLAlchemyActivationStore
from hovorunv2.infrastructure.persistence.stores.ban_store import SQLAlchemyBanStore
from hovorunv2.infrastructure.persistence.stores.beta_store import SQLAlchemyBetaEntitlementStore
from hovorunv2.infrastructure.persistence.stores.feature_fact_store import SQLAlchemyFeatureFactStore
from hovorunv2.infrastructure.persistence.stores.group_beta_store import SQLAlchemyGroupBetaStore
from hovorunv2.infrastructure.persistence.stores.group_grant_store import SQLAlchemyGroupGrantStore
from hovorunv2.infrastructure.persistence.stores.group_identity_store import SQLAlchemyGroupIdentityStore
from hovorunv2.infrastructure.persistence.stores.group_migration_store import SQLAlchemyGroupMigrationStore
from hovorunv2.infrastructure.persistence.stores.membership_store import SQLAlchemyMembershipStore
from hovorunv2.infrastructure.persistence.stores.usage_ledger_store import SQLAlchemyUsageLedgerStore
from hovorunv2.infrastructure.persistence.stores.usage_policy_store import SQLAlchemyUsagePolicyStore
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.administration import AdministrationCommand
from hovorunv2.interface.telegram.handlers.base import BaseCommand, RichMediaCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.debug import DebugCommand
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.handlers.membership import MembershipEventHandler
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.identity import IdentityMiddleware
from hovorunv2.interface.telegram.membership import TelegramMembershipAdapter
from hovorunv2.interface.telegram.prompts import TelegramUnauthorizedPromptSender

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


class InfrastructureProvider(DatabaseProvider):
    """Provider for core infrastructure services."""

    @provide(scope=Scope.APP)
    def get_settings(self) -> Settings:
        """Provide application settings."""
        return Settings()

    @provide(scope=Scope.APP)
    async def get_bot(self, config: Settings) -> AsyncIterable[Bot]:
        """Share one Telegram transport and close it after its consumers."""
        async with Bot(token=config.bot_token) as bot:
            yield bot

    @provide(scope=Scope.APP)
    async def get_http_session(self) -> AsyncIterable[aiohttp.ClientSession]:
        """Provide aiohttp ClientSession."""
        async with aiohttp.ClientSession() as session:
            yield session

    @provide(scope=Scope.APP)
    async def get_cache_service(self, config: Settings) -> AsyncIterable[CacheService]:
        """Provide CacheService (Valkey)."""
        service = CacheService(config.valkey_url)
        yield service
        await service.close()

    @provide(scope=Scope.APP)
    async def feature_queue(
        self,
        config: Settings,
        clock: Clock,
        namespace: RuntimeNamespace,
        safety: UsageSafetyCaps,
    ) -> AsyncIterable[FeatureQueuePort]:
        """Own a separate typed feature queue connection without starting a worker prematurely."""
        queue = CeleryFeatureQueue(config, clock, namespace, safety)
        yield queue
        await queue.close()

    membership_adapter = provide(TelegramMembershipAdapter, scope=Scope.APP)
    membership_port = alias(source=TelegramMembershipAdapter, provides=MembershipPort)

    database_settings = alias(source=Settings, provides=DatabaseSettings)
    settings_port = alias(source=Settings, provides=AppSettings)
    cache_port = alias(source=CacheService, provides=CachePort)


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


class IdentityAndMembershipProvider(Provider):
    """Provide identity mapping, chat lifecycle, activation, and membership reconciliation."""

    scope = Scope.APP

    chat_store_impl = provide(SQLAlchemyChatStore)
    chat_store = alias(source=SQLAlchemyChatStore, provides=ChatStore)

    activation_store_impl = provide(SQLAlchemyActivationStore)
    activation_store = alias(source=SQLAlchemyActivationStore, provides=ActivationStore)
    group_activation_service = provide(GroupActivationService)

    membership_store_impl = provide(SQLAlchemyMembershipStore)
    membership_store = alias(source=SQLAlchemyMembershipStore, provides=MembershipStore)

    group_grant_store_impl = provide(SQLAlchemyGroupGrantStore)
    group_grant_store = alias(source=SQLAlchemyGroupGrantStore, provides=GroupGrantStore)

    group_identity_store_impl = provide(SQLAlchemyGroupIdentityStore)
    group_identity_store = alias(source=SQLAlchemyGroupIdentityStore, provides=GroupIdentityStore)

    group_migration_store_impl = provide(SQLAlchemyGroupMigrationStore)
    group_migration_store = alias(source=SQLAlchemyGroupMigrationStore, provides=GroupMigrationStore)

    identity_middleware = provide(IdentityMiddleware)
    membership_event_handler = provide(MembershipEventHandler)
    membership_reconciliation = provide(MembershipReconciliationService)


class AuditAndPersistenceProvider(Provider):
    """Provide audited transactions, command audit, user identities and handle persistence."""

    scope = Scope.APP

    command_store_impl = provide(SQLAlchemyCommandStore)
    command_store = alias(source=SQLAlchemyCommandStore, provides=CommandStore)

    system_store_impl = provide(SQLAlchemySystemStore)
    system_store = alias(source=SQLAlchemySystemStore, provides=SystemStore)

    user_store_impl = provide(SQLAlchemyUserStore)
    user_store = alias(source=SQLAlchemyUserStore, provides=UserStore)

    public_handle_store_impl = provide(SQLAlchemyPublicHandleStore)
    public_handle_store = alias(source=SQLAlchemyPublicHandleStore, provides=PublicHandleStore)

    audited_transaction_impl = provide(SQLAlchemyAuditedTransaction)
    audited_transaction = alias(source=SQLAlchemyAuditedTransaction, provides=AuditedUnitOfWork)


class RuntimeUsageProvider(Provider):
    """Provide atomic rate limits, execution budgets, admission, and quota ledgers."""

    scope = Scope.APP

    @provide
    def runtime_namespace(self) -> RuntimeNamespace:
        """Keep runtime atomic operations in an isolated common hash slot."""
        return RuntimeNamespace()

    clock_impl = provide(WallClock)
    clock = alias(source=WallClock, provides=Clock)

    admission_store_impl = provide(ValkeyAdmissionStore)
    admission_store = alias(source=ValkeyAdmissionStore, provides=AdmissionPort)
    admission_service = provide(AdmissionService)

    payload_store_impl = provide(ValkeyPayloadStore)
    payload_store = alias(source=ValkeyPayloadStore, provides=PayloadPort)

    budget_store_impl = provide(ValkeyBudgetStore)
    budget_store = alias(source=ValkeyBudgetStore, provides=BudgetPort)
    budget_service = provide(BudgetService)

    usage_ledger_store_impl = provide(SQLAlchemyUsageLedgerStore)
    usage_ledger_store = alias(source=SQLAlchemyUsageLedgerStore, provides=UsageLedgerStore)

    usage_policy_store_impl = provide(SQLAlchemyUsagePolicyStore)
    usage_policy_store = alias(source=SQLAlchemyUsagePolicyStore, provides=UsagePolicyStore)
    usage_policy_service = provide(UsagePolicyService)

    @provide
    def usage_safety_caps(self) -> UsageSafetyCaps:
        """Provide application ceilings separately from ordinary quota overrides."""
        return UsageSafetyCaps()


class MediaAndContentProvider(Provider):
    """Provide media providers and content translation services."""

    scope = Scope.APP

    translation_provider = provide(GoogleTranslationService, provides=BaseTranslationService)
    translation_service = provide(TranslationService)

    @provide(scope=Scope.APP)
    def get_media_clients(self, settings: Settings) -> list[BaseMediaClient]:
        """Provide registered media provider clients for bot routing."""
        return list(default_media_clients(settings).values())

    @provide(scope=Scope.APP)
    def get_rich_media_commands(
        self,
        media_clients: list[BaseMediaClient],
        admission_service: AdmissionService,
        payload_store: PayloadPort,
        chat_store: ChatStore,
        user_store: UserStore,
        clock: Clock,
    ) -> list[RichMediaCommand]:
        """Wrap all registered media clients into generalized RichMediaCommand handlers."""
        return [
            RichMediaCommand(
                primary_client=client,
                admission=admission_service,
                payloads=payload_store,
                chats=chat_store,
                users=user_store,
                clock=clock,
            )
            for client in media_clients
        ]


class InterfaceAndUtilityProvider(Provider):
    """Provide bot handlers, utility commands, notification and administration services."""

    scope = Scope.APP

    prompt_sender_impl = provide(TelegramUnauthorizedPromptSender)
    prompt_sender = alias(source=TelegramUnauthorizedPromptSender, provides=UnauthorizedPromptSender)

    message_service = provide(MessageService)
    notification_service = provide(NotificationService)
    owner_notifier = provide(OwnerNotifier)
    reminder_service = provide(ChatReminderService)
    feedback_service = provide(FeedbackService)
    command_service = provide(CommandService)
    chat_status_service = provide(ChatStatusService)
    cleanup_service = provide(CleanupService)
    language_service = provide(LanguageService)

    administration_service = provide(AdministrationService)
    admin_sessions = provide(AdminSessionService)
    administration_command = provide(AdministrationCommand)

    settings_sessions = provide(SettingsSessionService)
    settings_command = provide(SettingsCommand)

    bot_join_handler = provide(BotJoinHandler)
    debug_command = provide(DebugCommand)
    help_command = provide(HelpCommand)
    feedback_command = provide(FeedbackCommand)
    group_management = provide(GroupManagementService)
    access_command = provide(AccessCommand)

    @provide(scope=Scope.APP)
    def get_utility_commands(
        self,
        help_command: HelpCommand,
        feedback_command: FeedbackCommand,
        settings_command: SettingsCommand,
        debug_command: DebugCommand,
        administration_command: AdministrationCommand,
    ) -> UtilityCommands:
        """Provide list of utility/infrastructure commands."""
        return UtilityCommands(
            [
                help_command,
                feedback_command,
                settings_command,
                debug_command,
                administration_command,
            ]
        )

    @provide(scope=Scope.APP)
    def get_commands(
        self,
        access_command: AccessCommand,
        rich_media_commands: list[RichMediaCommand],
        utility_commands: UtilityCommands,
    ) -> list[BaseCommand]:
        """Provide list of all bot commands."""
        return [
            access_command,
            *rich_media_commands,
            *utility_commands,
        ]


class AppProvider(
    SecurityAndAccessProvider,
    IdentityAndMembershipProvider,
    AuditAndPersistenceProvider,
    RuntimeUsageProvider,
    MediaAndContentProvider,
    InterfaceAndUtilityProvider,
):
    """Unified application provider composing domain, persistence and service modules."""
