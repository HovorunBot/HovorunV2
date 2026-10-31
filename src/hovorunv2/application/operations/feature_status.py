"""Public outage and recovery notices for scopes actually using a feature."""

from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.emergency import FeatureEmergencyState
from hovorunv2.application.features.registry import FeatureRegistry
from hovorunv2.application.jobs.feature_jobs import PayloadPort, PayloadState
from hovorunv2.application.operations.notification_ledger import (
    NotificationEvent,
    NotificationLedgerService,
    NotificationOutcome,
)
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext

BODY_TTL_SECONDS = 300


class FeatureAudienceStore(Protocol):
    """Select only the scopes that actually use the affected feature."""

    async def selected_targets(self, feature_key: str) -> tuple[PermissionTarget, ...]:
        """Return active users and approved groups with an enabled split of the feature."""
        ...

    async def announced(self, event_key: str) -> bool:
        """Return whether this transition event already exists."""
        ...


class FeatureStatusNotifier:
    """Create one public-safe outage/recovery event per emergency transition."""

    def __init__(
        self,
        audience: FeatureAudienceStore,
        ledger: NotificationLedgerService,
        bodies: PayloadPort,
        registry: FeatureRegistry,
        clock: Clock,
    ) -> None:
        self._audience = audience
        self._ledger = ledger
        self._bodies = bodies
        self._registry = registry
        self._clock = clock

    async def publish(
        self,
        state: FeatureEmergencyState,
        *,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> int:
        """Queue one notice to the affected scopes; internal-only stops publish nothing."""
        if state.disabled and state.public_reason is None:
            return 0
        targets = await self._audience.selected_targets(state.feature_key)
        if not targets:
            return 0
        event_key = self._event_key(state)
        if await self._audience.announced(event_key):
            return 0
        sealed = await self._bodies.seal(event_key, self._render(state).encode(), self._clock.now() + BODY_TTL_SECONDS)
        if await self._bodies.put(sealed) != PayloadState.READY:
            return 0
        event = NotificationEvent(
            event_key,
            NotificationCategory.FEATURE_STATUS,
            "feature_status",
            state.feature_key,
            sealed.reference,
        )
        await self._ledger.ensure_event(event, actor=actor)
        for target in targets:
            await self._ledger.record(
                event_key,
                target,
                NotificationOutcome.PENDING,
                attempts=0,
                actor=actor,
            )
        return len(targets)

    def _event_key(self, state: FeatureEmergencyState) -> str:
        """Bind one logical announcement to the transition epoch, not the transition count."""
        status = "disabled" if state.disabled else "enabled"
        return f"feature-status.{state.feature_key}.{status}.{state.epoch}"

    def _render(self, state: FeatureEmergencyState) -> str:
        """Render only the required public-safe text; internal reasons never appear."""
        definition = self._registry.require(state.feature_key)
        if state.disabled:
            return f"⚠️ <b>{definition.display_name}</b> is temporarily unavailable: {state.public_reason}"
        return f"✅ <b>{definition.display_name}</b> is available again."
