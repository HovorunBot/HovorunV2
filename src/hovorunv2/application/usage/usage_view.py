"""Aggregate feature usage and effective allowances for settings views."""

from dataclasses import dataclass

from hovorunv2.application.features.registry import FeatureRegistry, TriggerKind
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.application.usage.policies import UsageSafetyCaps, UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageLedgerStore
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.domain.enums import TargetKind


@dataclass(frozen=True, slots=True)
class FeatureUsage:
    """One feature's current-window total and effective remaining allowance."""

    feature: str
    display_name: str
    unit: UsageUnit
    window_seconds: int
    consumed: int
    limit: int
    remaining: int


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Aggregate operational allowance view; never a queue listing or message contents."""

    subject_kind: TargetKind
    subject_telegram_id: int
    unlimited_personal_quota: bool
    safety: UsageSafetyCaps
    features: tuple[FeatureUsage, ...]


class UsageViewService:
    """Read current totals and effective limits for one charged subject."""

    def __init__(
        self,
        registry: FeatureRegistry,
        policies: UsagePolicyService,
        ledger: UsageLedgerStore,
        safety: UsageSafetyCaps,
        clock: Clock,
    ) -> None:
        self._registry = registry
        self._policies = policies
        self._ledger = ledger
        self._safety = safety
        self._clock = clock

    async def for_user(self, actor_id: int) -> UsageSummary:
        """Return the actor's own direct-work allowance in the current windows."""
        return await self._summary(
            AccountingSubject(TargetKind.USER, actor_id),
            TriggerKind.DIRECT,
            actor_id=actor_id,
            group_id=None,
        )

    async def for_group(self, group_id: int) -> UsageSummary:
        """Return group-charged automatic work only, never another member's personal usage."""
        return await self._summary(
            AccountingSubject(TargetKind.GROUP, group_id),
            TriggerKind.AUTOMATIC,
            actor_id=None,
            group_id=group_id,
        )

    async def _summary(
        self,
        subject: AccountingSubject,
        trigger: TriggerKind,
        *,
        actor_id: int | None,
        group_id: int | None,
    ) -> UsageSummary:
        """Resolve the same policies as execution and read their current-window totals."""
        observed = int(self._clock.now())
        features: list[FeatureUsage] = []
        unlimited = False
        for definition in self._registry.all():
            if UsageUnit.REQUESTS not in definition.units:
                continue
            resolved = await self._policies.resolve(definition.key, trigger, actor_id, group_id=group_id)
            unlimited = resolved.bypass_personal_quota
            totals = [
                await self._ledger.total(subject, policy.bucket, policy.unit, policy.window, observed)
                for policy in resolved.policies
            ]
            features.append(
                FeatureUsage(
                    definition.key,
                    definition.display_name,
                    UsageUnit.REQUESTS,
                    min(policy.window.seconds for policy in resolved.policies),
                    max(totals),
                    min(policy.limit for policy in resolved.policies),
                    max(0, min(policy.limit - total for policy, total in zip(resolved.policies, totals, strict=True))),
                )
            )
        return UsageSummary(subject.kind, subject.telegram_id, unlimited, self._safety, tuple(features))
