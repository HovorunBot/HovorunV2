"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, alias, provide

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.operations.ports import SystemStore
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyCommandStore,
    SQLAlchemyPublicHandleStore,
    SQLAlchemySystemStore,
    SQLAlchemyUserStore,
)
from hovorunv2.interface.telegram.handlers.base import BaseCommand

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


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
