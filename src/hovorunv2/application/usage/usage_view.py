"""Aggregate feature usage and effective allowances for settings views."""

from dataclasses import dataclass

from hovorunv2.application.features.registry import FeatureRegistry, TriggerKind
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.application.usage.policies import MEDIA_BUCKET, UsagePolicy, UsageSafetyCaps, UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageLedgerStore
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.domain.enums import TargetKind


@dataclass(frozen=True, slots=True)
class FeatureUsage:
    """One distinct quota bucket and window with its current allowance."""

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
        """Return the actor's own direct-work allowance in the current windows.

        Args:
            actor_id: Telegram ID of the user.

        Returns:
            The user's usage summary.
        """
        return await self._summary(
            AccountingSubject(TargetKind.USER, actor_id),
            TriggerKind.DIRECT,
            actor_id=actor_id,
            group_id=None,
        )

    async def for_group(self, group_id: int) -> UsageSummary:
        """Return group-charged automatic work, never another member's personal usage.

        Args:
            group_id: Telegram ID of the group.

        Returns:
            The group's usage summary.
        """
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
        """Resolve the same policies as execution and read their current-window totals.

        Args:
            subject: Charged accounting subject.
            trigger: How the work enters the application.
            actor_id: Telegram ID of the direct actor, if any.
            group_id: Telegram ID of the automatic group, if any.

        Returns:
            The aggregate usage summary.
        """
        observed = int(self._clock.now())
        features: list[FeatureUsage] = []
        seen: set[tuple[str, UsageUnit, int]] = set()
        unlimited = False
        for definition in self._registry.all():
            if UsageUnit.REQUESTS not in definition.units:
                continue
            if definition.provider is not None:
                resolved = await self._policies.resolve_media(definition.provider, trigger, actor_id, group_id=group_id)
            else:
                resolved = await self._policies.resolve(definition.key, trigger, actor_id, group_id=group_id)
            unlimited = resolved.bypass_personal_quota
            for policy in resolved.policies:
                dimension = (policy.bucket, policy.unit, policy.window.seconds)
                if dimension in seen:
                    continue
                seen.add(dimension)
                name = (
                    "Media uploads — shared across all providers"
                    if policy.bucket == MEDIA_BUCKET
                    else definition.display_name
                )
                features.append(await self._quota_entry(subject, policy, name, observed))
        return UsageSummary(subject.kind, subject.telegram_id, unlimited, self._safety, tuple(features))

    async def _quota_entry(
        self, subject: AccountingSubject, policy: UsagePolicy, display_name: str, observed: int
    ) -> FeatureUsage:
        """Read consumption for one actual quota bucket and window.

        Args:
            subject: Charged accounting subject.
            policy: Effective quota policy.
            display_name: User-facing label for the shared or feature quota.
            observed: Timestamp used consistently throughout the summary.

        Returns:
            Consumption and remaining allowance for the exact policy dimension.
        """
        consumed = await self._ledger.total(subject, policy.bucket, policy.unit, policy.window, observed)
        return FeatureUsage(
            policy.bucket,
            display_name,
            policy.unit,
            policy.window.seconds,
            consumed,
            policy.limit,
            max(0, policy.limit - consumed),
        )
