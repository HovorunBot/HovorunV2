"""Dependency injection configuration using dishka."""

from collections.abc import AsyncIterable
from typing import NewType

import aiohttp
from aiogram import Bot
from dishka import Provider, Scope, alias, provide

from hovorunv2.application.activation import ActivationStore
from hovorunv2.application.admission import AdmissionPort
from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.beta import BetaEntitlementStore
from hovorunv2.application.budgets import BudgetPort, Clock, RuntimeNamespace, WallClock
from hovorunv2.application.clients.base import FACEBOOK_CONFIG, BaseMediaClient
from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.clients.reddit import RedditService
from hovorunv2.application.clients.threads import ThreadsService
from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.clients.youtube import YoutubeService
from hovorunv2.application.feature_jobs import FeatureQueuePort, PayloadPort
from hovorunv2.application.features import FEATURE_REGISTRY, FeatureRegistry
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.group_migration import GroupMigrationStore
from hovorunv2.application.identity import GroupIdentityStore
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.media.pipeline import MediaPipeline
from hovorunv2.application.membership import MembershipPort
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.policy_facts import FeatureFactStore
from hovorunv2.application.ports import (
    AppSettings,
    AuditedUnitOfWork,
    AuthorizationPolicy,
    BrowserPort,
    CachePort,
    ChatStore,
    CommandStore,
    PublicHandleStore,
    RoleStore,
    SystemStore,
    TaskQueuePort,
    UnauthorizedPromptSender,
    UserStore,
)
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.activation_service import GroupActivationService
from hovorunv2.application.services.admin_session_service import AdminSessionService
from hovorunv2.application.services.administration_service import AdministrationService
from hovorunv2.application.services.admission_service import AdmissionService
from hovorunv2.application.services.ban_service import BanService
from hovorunv2.application.services.beta_service import BetaEligibilityService
from hovorunv2.application.services.budget_service import BudgetService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.feedback_service import FeedbackService
from hovorunv2.application.services.group_beta_service import GroupBetaService
from hovorunv2.application.services.group_management_service import GroupManagementService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.application.services.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.notification_service import NotificationService
from hovorunv2.application.services.owner_notifier import OwnerNotifier
from hovorunv2.application.services.permission_service import PermissionService
from hovorunv2.application.services.reminder_service import ChatReminderService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.application.services.settings_session_service import SettingsSessionService
from hovorunv2.application.services.translation_providers import (
    BaseTranslationService,
    GoogleTranslationService,
)
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.application.services.usage_policy_service import UsagePolicyService
from hovorunv2.application.usage import UsagePolicyStore, UsageSafetyCaps
from hovorunv2.application.usage_ledger import UsageLedgerStore
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.infrastructure.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.browser import BrowserService
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
from hovorunv2.infrastructure.persistence.activation_store import SQLAlchemyActivationStore
from hovorunv2.infrastructure.persistence.ban_store import SQLAlchemyBanStore
from hovorunv2.infrastructure.persistence.beta_store import SQLAlchemyBetaEntitlementStore
from hovorunv2.infrastructure.persistence.feature_fact_store import SQLAlchemyFeatureFactStore
from hovorunv2.infrastructure.persistence.group_beta_store import SQLAlchemyGroupBetaStore
from hovorunv2.infrastructure.persistence.group_grant_store import SQLAlchemyGroupGrantStore
from hovorunv2.infrastructure.persistence.group_identity_store import SQLAlchemyGroupIdentityStore
from hovorunv2.infrastructure.persistence.group_migration_store import SQLAlchemyGroupMigrationStore
from hovorunv2.infrastructure.persistence.membership_store import SQLAlchemyMembershipStore
from hovorunv2.infrastructure.persistence.usage_ledger_store import SQLAlchemyUsageLedgerStore
from hovorunv2.infrastructure.persistence.usage_policy_store import SQLAlchemyUsagePolicyStore
from hovorunv2.infrastructure.task_queue import TaskQueueService
from hovorunv2.interface.telegram.delivery import TelegramMediaDelivery
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
    async def get_browser_service(self, config: Settings) -> AsyncIterable[BrowserService]:
        """Provide BrowserService (DrissionPage)."""
        service = BrowserService(
            max_tabs=config.browser_max_tabs,
            idle_timeout=config.browser_idle_timeout,
        )
        yield service
        await service.close()

    @provide(scope=Scope.APP)
    async def get_task_queue_service(self, config: Settings) -> AsyncIterable[TaskQueueService]:
        """Provide TaskQueueService (SAQ)."""
        service = TaskQueueService(config)
        yield service
        await service.stop_worker()

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
    browser_port = alias(source=BrowserService, provides=BrowserPort)
    task_queue_port = alias(source=TaskQueueService, provides=TaskQueuePort)


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
    """Provide media extraction, pipeline processing, downloads and provider adapters."""

    scope = Scope.APP

    media_downloader = provide(MediaDownloader)
    media_extractor = provide(MediaExtractor)
    media_pipeline = provide(MediaPipeline)

    tiktok_service = provide(TikTokService)
    twitter_service = provide(TwitterService)
    threads_service = provide(ThreadsService)
    instagram_service = provide(InstagramService)
    bluesky_service = provide(BlueskyService)
    youtube_service = provide(YoutubeService)
    reddit_service = provide(RedditService)

    translation_provider = provide(GoogleTranslationService, provides=BaseTranslationService)
    translation_service = provide(TranslationService)

    @provide(scope=Scope.APP)
    def get_media_clients(
        self,
        session: aiohttp.ClientSession,
        browser: BrowserPort,
        bluesky_service: BlueskyService,
        instagram_service: InstagramService,
        threads_service: ThreadsService,
        tiktok_service: TikTokService,
        twitter_service: TwitterService,
        youtube_service: YoutubeService,
        reddit_service: RedditService,
    ) -> list[BaseMediaClient]:
        """Provide all registered media provider clients."""
        return [
            bluesky_service,
            BaseMediaClient(config=FACEBOOK_CONFIG, http=session, browser=browser),
            instagram_service,
            threads_service,
            tiktok_service,
            twitter_service,
            youtube_service,
            reddit_service,
        ]

    @provide(scope=Scope.APP)
    def get_telegram_media_delivery(
        self,
        media_downloader: MediaDownloader,
        session: aiohttp.ClientSession,
    ) -> TelegramMediaDelivery:
        """Provide Telegram media delivery adapter."""
        return TelegramMediaDelivery(media_downloader=media_downloader, session=session)

    @provide(scope=Scope.APP)
    def get_rich_media_commands(
        self,
        media_clients: list[BaseMediaClient],
        pipeline: MediaPipeline,
        media_delivery: TelegramMediaDelivery,
    ) -> list[RichMediaCommand]:
        """Wrap all registered media clients into generalized RichMediaCommand handlers."""
        return [
            RichMediaCommand(
                primary_client=client,
                pipeline=pipeline,
                media_delivery=media_delivery,
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
