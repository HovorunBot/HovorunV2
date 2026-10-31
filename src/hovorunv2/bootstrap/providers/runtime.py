"""Dependency injection configuration using dishka."""

from typing import NewType

from dishka import Provider, Scope, alias, provide

from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.jobs.admission import AdmissionPort
from hovorunv2.application.jobs.admission_service import AdmissionService
from hovorunv2.application.jobs.feature_jobs import PayloadPort
from hovorunv2.application.usage.budget_service import BudgetService
from hovorunv2.application.usage.budgets import BudgetPort, Clock, RuntimeNamespace, WallClock
from hovorunv2.application.usage.policies import UsagePolicyStore, UsageSafetyCaps
from hovorunv2.application.usage.usage_administration import UsageAdministrationService
from hovorunv2.application.usage.usage_ledger import UsageLedgerStore
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.application.usage.usage_view import UsageViewService
from hovorunv2.infrastructure.persistence.stores.usage_ledger_store import SQLAlchemyUsageLedgerStore
from hovorunv2.infrastructure.persistence.stores.usage_policy_store import SQLAlchemyUsagePolicyStore
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.budgets import ValkeyBudgetStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from hovorunv2.interface.telegram.handlers.base import BaseCommand

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


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
    feature_emergency_service = provide(FeatureEmergencyService)
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
    usage_administration_service = provide(UsageAdministrationService)
    usage_view_service = provide(UsageViewService)

    @provide
    def usage_safety_caps(self) -> UsageSafetyCaps:
        """Provide application ceilings separately from ordinary quota overrides."""
        return UsageSafetyCaps()
