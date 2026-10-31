"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, alias, provide

from hovorunv2.application.groups.activation import ActivationStore
from hovorunv2.application.groups.activation_service import GroupActivationService
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.group_migration import GroupMigrationStore
from hovorunv2.application.identity.identity import GroupIdentityStore
from hovorunv2.application.identity.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.ports import (
    ChatStore,
)
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyChatStore,
)
from hovorunv2.infrastructure.persistence.stores.activation_store import SQLAlchemyActivationStore
from hovorunv2.infrastructure.persistence.stores.group_grant_store import SQLAlchemyGroupGrantStore
from hovorunv2.infrastructure.persistence.stores.group_identity_store import SQLAlchemyGroupIdentityStore
from hovorunv2.infrastructure.persistence.stores.group_migration_store import SQLAlchemyGroupMigrationStore
from hovorunv2.infrastructure.persistence.stores.membership_store import SQLAlchemyMembershipStore
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.membership import MembershipEventHandler
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


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
