"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, alias, provide

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.beta_prompts import BetaPromptStore
from hovorunv2.application.features.emergency import FeatureEmergencyStore
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.operations.feature_status import FeatureAudienceStore
from hovorunv2.application.operations.notification_ledger import NotificationEventStore
from hovorunv2.application.operations.notification_preferences import NotificationPreferenceStore
from hovorunv2.application.operations.ports import SystemStore
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyCommandStore,
    SQLAlchemyPublicHandleStore,
    SQLAlchemySystemStore,
    SQLAlchemyUserStore,
)
from hovorunv2.infrastructure.persistence.stores.beta_prompt_store import SQLAlchemyBetaPromptStore
from hovorunv2.infrastructure.persistence.stores.feature_audience_store import SQLAlchemyFeatureAudienceStore
from hovorunv2.infrastructure.persistence.stores.feature_emergency_store import SQLAlchemyFeatureEmergencyStore
from hovorunv2.infrastructure.persistence.stores.notification_ledger_store import (
    SQLAlchemyNotificationLedgerStore,
)
from hovorunv2.infrastructure.persistence.stores.notification_preference_store import (
    SQLAlchemyNotificationPreferenceStore,
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

    feature_emergency_store_impl = provide(SQLAlchemyFeatureEmergencyStore)
    feature_emergency_store = alias(source=SQLAlchemyFeatureEmergencyStore, provides=FeatureEmergencyStore)

    feature_audience_store_impl = provide(SQLAlchemyFeatureAudienceStore)
    feature_audience_store = alias(source=SQLAlchemyFeatureAudienceStore, provides=FeatureAudienceStore)

    beta_prompt_store_impl = provide(SQLAlchemyBetaPromptStore)
    beta_prompt_store = alias(source=SQLAlchemyBetaPromptStore, provides=BetaPromptStore)

    notification_preference_store_impl = provide(SQLAlchemyNotificationPreferenceStore)
    notification_preference_store = alias(
        source=SQLAlchemyNotificationPreferenceStore, provides=NotificationPreferenceStore
    )

    notification_ledger_store_impl = provide(SQLAlchemyNotificationLedgerStore)
    notification_ledger_store = alias(source=SQLAlchemyNotificationLedgerStore, provides=NotificationEventStore)

    audited_transaction_impl = provide(SQLAlchemyAuditedTransaction)
    audited_transaction = alias(source=SQLAlchemyAuditedTransaction, provides=AuditedUnitOfWork)
