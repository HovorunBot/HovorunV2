"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, provide

from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_job_runner import default_media_clients
from hovorunv2.application.jobs.feature_jobs import PayloadPort
from hovorunv2.application.media.providers.base import BaseMediaClient
from hovorunv2.application.ports import (
    ChatStore,
    UserStore,
)
from hovorunv2.application.translation.providers import (
    BaseTranslationService,
    GoogleTranslationService,
)
from hovorunv2.application.translation.service import TranslationService
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.base import BaseCommand, RichMediaCommand

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


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
