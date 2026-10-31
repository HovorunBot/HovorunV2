"""Audited scoped usage-limit inspection and overrides for administrators."""

from dataclasses import dataclass

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.usage.policies import (
    ResolvedUsage,
    UsagePolicy,
    UsagePolicyStore,
    UsageSafetyCaps,
    UsageUnit,
)
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind

DEFAULT_SOURCE = "default"
OVERRIDE_SOURCE = "override"


@dataclass(frozen=True, slots=True)
class UsagePolicyView:
    """One effective policy line and whether a stored override produced it."""

    bucket: str
    unit: UsageUnit
    window_seconds: int
    limit: int
    burst: int
    concurrency: int
    pending: int
    source: str


@dataclass(frozen=True, slots=True)
class UsageLimitsView:
    """Effective limits and independent safety caps for one target and feature."""

    target_kind: str
    target_telegram_id: int
    feature: str
    unit: UsageUnit
    bypass_personal_quota: bool
    safety: UsageSafetyCaps
    policies: tuple[UsagePolicyView, ...]

    def as_dict(self) -> dict[str, object]:
        """Render an operational summary without private message contents."""
        return {
            "target_kind": self.target_kind,
            "target_telegram_id": self.target_telegram_id,
            "feature": self.feature,
            "unit": self.unit.value,
            "bypass_personal_quota": self.bypass_personal_quota,
            "safety": self._safety_dict(),
            "policies": [self._policy_dict(view) for view in self.policies],
        }

    def _safety_dict(self) -> dict[str, int]:
        """Expose application ceilings separately from ordinary quota records."""
        return {
            "global_concurrency": self.safety.global_concurrency,
            "global_pending": self.safety.global_pending,
            "payload_bytes": self.safety.payload_bytes,
            "total_payload_bytes": self.safety.total_payload_bytes,
            "deadline_seconds": self.safety.deadline_seconds,
            "execution_seconds": self.safety.execution_seconds,
        }

    def _policy_dict(self, view: UsagePolicyView) -> dict[str, object]:
        """Serialize one policy line with its resolved default/override source."""
        return {
            "bucket": view.bucket,
            "unit": view.unit.value,
            "window_seconds": view.window_seconds,
            "limit": view.limit,
            "burst": view.burst,
            "concurrency": view.concurrency,
            "pending": view.pending,
            "source": view.source,
        }


class UsageAdministrationService:
    """Read effective allowances and apply audited overrides through their owning store."""

    def __init__(self, policies: UsagePolicyService, store: UsagePolicyStore, safety: UsageSafetyCaps) -> None:
        self._policies = policies
        self._store = store
        self._safety = safety

    async def inspect(
        self, target: PermissionTarget, feature: str, unit: UsageUnit = UsageUnit.REQUESTS
    ) -> UsageLimitsView:
        """Resolve current defaults and overrides without granting execution or unlimited quota."""
        if feature == "*":
            msg = "Limit inspection requires an explicit feature key."
            raise ValueError(msg)
        resolved = await self._resolve(target, feature, unit)
        overrides = await self._store.list_overrides(target)
        override_keys = {(policy.bucket, policy.unit, policy.window.seconds) for policy in overrides}
        views = tuple(
            UsagePolicyView(
                policy.bucket,
                policy.unit,
                policy.window.seconds,
                policy.limit,
                policy.burst,
                policy.concurrency,
                policy.pending,
                OVERRIDE_SOURCE
                if (policy.bucket, policy.unit, policy.window.seconds) in override_keys
                else DEFAULT_SOURCE,
            )
            for policy in resolved.policies
        )
        return UsageLimitsView(
            target.kind,
            target.telegram_id or 0,
            feature,
            unit,
            resolved.bypass_personal_quota,
            resolved.safety,
            views,
        )

    async def _resolve(self, target: PermissionTarget, feature: str, unit: UsageUnit) -> ResolvedUsage:
        """Resolve through the same charged-subject rules as execution."""
        if target.kind == TargetKind.USER.value:
            return await self._policies.resolve(feature, TriggerKind.DIRECT, target.telegram_id, unit=unit)
        if target.kind == TargetKind.GROUP.value:
            return await self._policies.resolve(
                feature, TriggerKind.AUTOMATIC, None, group_id=target.telegram_id, unit=unit
            )
        msg = "Usage limits apply only to users and groups."
        raise ValueError(msg)

    async def set_override(
        self,
        target: PermissionTarget,
        policy: UsagePolicy,
        *,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        """Apply one audited override without resetting consumed usage."""
        return await self._store.set_override(target, policy, actor=actor, transport_kind=transport_kind)

    async def reset_override(
        self,
        target: PermissionTarget,
        policy: UsagePolicy,
        *,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        """Soft-delete one override so the current default applies again."""
        return await self._store.set_override(target, policy, actor=actor, transport_kind=transport_kind, remove=True)
