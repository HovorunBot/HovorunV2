"""Dependency injection configuration using dishka."""

from collections.abc import AsyncIterable
from typing import NewType

import aiohttp
from aiogram import Bot
from dishka import Scope, alias, provide

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.identity.membership import MembershipPort
from hovorunv2.application.jobs.feature_jobs import FeatureQueuePort
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.infrastructure.config import DatabaseSettings, Settings
from hovorunv2.infrastructure.queues.feature_queue import CeleryFeatureQueue
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.transport.membership import TelegramMembershipAdapter

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
