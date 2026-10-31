"""One-time beta introduction announcements and durable Activate/Decline decisions."""

import json
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.group_beta_service import GroupBetaService
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import FeatureChannel, FeatureDefinition, FeatureRegistry
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.jobs.feature_jobs import PayloadPort, PayloadState
from hovorunv2.application.operations.notification_ledger import (
    NotificationEvent,
    NotificationLedgerService,
    NotificationOutcome,
)
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin, TargetKind

BODY_TTL_SECONDS = 300
AUTHORITY_ACTION = "bot:administer"
SYSTEM_ACTOR = ActorContext.system("beta introduction")
MESSAGE = "Beta activation requires the eligible subject or current management authority."


class BetaDecision(StrEnum):
    """Recipient's durable choice for one announced beta feature."""

    ACTIVE = "active"
    DECLINED = "declined"


class BetaPromptStore(Protocol):
    """Persist decisions and enumerate the announced eligible subset."""

    async def decision(self, target: PermissionTarget, feature: str) -> BetaDecision | None:
        """Return the active decision for the exact subject generation, if any."""
        ...

    async def set_decision(
        self, target: PermissionTarget, feature: str, decision: BetaDecision, *, actor: ActorContext
    ) -> bool:
        """Upsert one decision atomically with audit."""
        ...

    async def eligible_targets(self, feature: str) -> tuple[PermissionTarget, ...]:
        """Return active eligible users and qualifying approved groups."""
        ...

    async def announced(self, event_key: str) -> bool:
        """Return whether the immutable introduction event already exists."""
        ...


class BetaPromptService:
    """Announce each beta feature once and keep decisions independent of eligibility."""

    def __init__(
        self,
        store: BetaPromptStore,
        registry: FeatureRegistry,
        beta: BetaEligibilityService,
        group_beta: GroupBetaService,
        commands: CommandStore,
        groups: GroupManagementService,
        permissions: PermissionService,
        bans: BanStore,
        ledger: NotificationLedgerService,
        bodies: PayloadPort,
        clock: Clock,
    ) -> None:
        self._store = store
        self._registry = registry
        self._beta = beta
        self._group_beta = group_beta
        self._commands = commands
        self._groups = groups
        self._permissions = permissions
        self._bans = bans
        self._ledger = ledger
        self._bodies = bodies
        self._clock = clock

    async def announce_new(self) -> int:
        """Announce every beta feature that has no introduction event yet."""
        announced = 0
        for definition in self._registry.all():
            if definition.channel == FeatureChannel.BETA:
                announced += await self.announce(definition.key)
        return announced

    async def announce(self, feature: str) -> int:
        """Create one immutable event and pending receipts for the eligible subset."""
        definition = self._registry.require(feature)
        if definition.channel != FeatureChannel.BETA:
            return 0
        event_key = f"beta.{feature}"
        if await self._store.announced(event_key):
            return 0
        targets = await self._store.eligible_targets(feature)
        if not targets:
            return 0
        sealed = await self._bodies.seal(
            event_key, self._render(definition).encode(), self._clock.now() + BODY_TTL_SECONDS
        )
        if await self._bodies.put(sealed) != PayloadState.READY:
            return 0
        event = NotificationEvent(
            event_key,
            NotificationCategory.BETA,
            "beta",
            definition.key,
            sealed.reference,
            self._markup(definition.key),
        )
        await self._ledger.ensure_event(event, actor=SYSTEM_ACTOR)
        for target in targets:
            await self._ledger.record(
                event_key,
                target,
                NotificationOutcome.PENDING,
                attempts=0,
                actor=SYSTEM_ACTOR,
            )
        return len(targets)

    async def decide(
        self,
        target: PermissionTarget,
        feature: str,
        decision: BetaDecision,
        *,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> bool:
        """Persist one decision and activate only the announced feature when accepted."""
        definition = self._registry.require(feature)
        if definition.channel != FeatureChannel.BETA:
            msg = "Beta decisions require a beta feature."
            raise ValueError(msg)
        await self._authorize(target, actor, transport_kind)
        if not await self._beta.is_eligible(target, feature):
            raise PermissionError(MESSAGE)
        if not await self._store.announced(f"beta.{feature}"):
            msg = "The feature was not announced to this subject."
            raise ValueError(msg)
        changed = await self._store.set_decision(target, feature, decision, actor=actor)
        if decision == BetaDecision.ACTIVE:
            await self._activate(target, feature, actor, transport_kind)
        return changed

    async def _activate(
        self,
        target: PermissionTarget,
        feature: str,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> None:
        """Enable exactly the announced feature through its existing selection boundaries."""
        if target.kind == TargetKind.USER.value:
            if target.telegram_id is None:
                msg = "Beta activation requires a known Telegram user."
                raise ValueError(msg)
            await self._commands.enable_command(target.telegram_id, feature, actor=actor)
            return
        await self._group_beta.set_enabled(target, feature, enabled=True, actor=actor, transport_kind=transport_kind)

    async def _authorize(self, target: PermissionTarget, actor: ActorContext, transport_kind: str | None) -> None:
        """Allow the subject privately, a current group manager, or a current bot administrator."""
        if actor.origin == ActorOrigin.CLI:
            return
        if actor.origin != ActorOrigin.TELEGRAM or actor.actor_id is None:
            raise PermissionError(MESSAGE)
        if await self._bans.list_active(TargetKind.USER, actor.actor_id):
            raise PermissionError(MESSAGE)
        if (
            target.kind == TargetKind.USER.value
            and actor.actor_id == target.telegram_id
            and transport_kind == "private"
        ):
            return
        if target.kind == TargetKind.GROUP.value and await self._groups.can_manage(actor.actor_id, target):
            return
        if await self._permissions.has_authority(actor.actor_id, AUTHORITY_ACTION):
            return
        raise PermissionError(MESSAGE)

    def _render(self, definition: FeatureDefinition) -> str:
        """Render the announced feature without internal notes or unannounced features."""
        return (
            f"🧪 <b>New beta feature: {definition.display_name}</b>\n\n"
            "Choose whether to activate it for this subject. "
            "Declining keeps it selectable later in settings."
        )

    def _markup(self, feature: str) -> str:
        """Build the fixed Activate/Decline/Go to features keyboard for this feature."""
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Activate", "callback_data": f"beta:activate:{feature}"},
                        {"text": "Decline", "callback_data": f"beta:decline:{feature}"},
                    ],
                    [{"text": "Go to features", "callback_data": f"beta:features:{feature}"}],
                ]
            },
            separators=(",", ":"),
        )
