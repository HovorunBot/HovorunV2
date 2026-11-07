"""Dependency injection configuration using dishka."""

from collections.abc import Sequence

from dishka import Provider, Scope, provide

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import PayloadPort
from hovorunv2.application.media.provider_config import (
    DEFAULT_PROVIDER_CONFIGS,
    ProviderConfig,
)
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand


class MediaAndContentProvider(Provider):
    """Provide media provider configurations and routing handlers."""

    scope = Scope.APP

    @provide(scope=Scope.APP)
    def get_provider_configs(self) -> Sequence[ProviderConfig]:
        """Provide registered media provider configurations for bot routing."""
        return DEFAULT_PROVIDER_CONFIGS

    @provide(scope=Scope.APP)
    def get_rich_media_commands(
        self,
        provider_configs: Sequence[ProviderConfig],
        admission_service: AdmissionService,
        payload_store: PayloadPort,
        chat_store: ChatStore,
        user_store: UserStore,
        clock: Clock,
    ) -> list[RichMediaCommand]:
        """Wrap all registered provider configs into generalized RichMediaCommand handlers."""
        return [
            RichMediaCommand(
                provider_config=config,
                admission=admission_service,
                payloads=payload_store,
                chats=chat_store,
                users=user_store,
                clock=clock,
            )
            for config in provider_configs
        ]
