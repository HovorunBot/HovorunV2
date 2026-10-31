"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, alias, provide

from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.access.ports import UnauthorizedPromptSender
from hovorunv2.application.features.beta_prompts import BetaPromptService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.messaging.feedback_service import FeedbackService
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.operations.announcements import AnnouncementService
from hovorunv2.application.operations.cleanup_service import CleanupService
from hovorunv2.application.operations.feature_status import FeatureStatusNotifier
from hovorunv2.application.operations.notification_ledger import NotificationLedgerService
from hovorunv2.application.operations.notification_preferences import NotificationPreferenceService
from hovorunv2.application.operations.notification_service import NotificationService
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.application.settings.admin_session_service import AdminSessionService
from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.application.translation.language_service import LanguageService
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.administration import AdministrationCommand
from hovorunv2.interface.telegram.handlers.announcements import AnnounceCommand
from hovorunv2.interface.telegram.handlers.base import BaseCommand, RichMediaCommand
from hovorunv2.interface.telegram.handlers.beta import BetaPromptCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.debug import DebugCommand
from hovorunv2.interface.telegram.handlers.emergency import EmergencyCommand
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.settings.prompts import TelegramUnauthorizedPromptSender

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


class InterfaceAndUtilityProvider(Provider):
    """Provide bot handlers, utility commands, notification and administration services."""

    scope = Scope.APP

    prompt_sender_impl = provide(TelegramUnauthorizedPromptSender)
    prompt_sender = alias(source=TelegramUnauthorizedPromptSender, provides=UnauthorizedPromptSender)

    message_service = provide(MessageService)
    notification_service = provide(NotificationService)
    notification_preferences = provide(NotificationPreferenceService)
    notification_ledger = provide(NotificationLedgerService)
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
    beta_prompts = provide(BetaPromptService)
    feature_status_notifier = provide(FeatureStatusNotifier)
    beta_prompt_command = provide(BetaPromptCommand)
    announcement_service = provide(AnnouncementService)
    announce_command = provide(AnnounceCommand)
    debug_command = provide(DebugCommand)
    emergency_command = provide(EmergencyCommand)
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
        beta_prompt_command: BetaPromptCommand,
        announce_command: AnnounceCommand,
        emergency_command: EmergencyCommand,
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
                emergency_command,
                beta_prompt_command,
                announce_command,
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
