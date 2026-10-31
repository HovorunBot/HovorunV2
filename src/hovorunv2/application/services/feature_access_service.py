"""Shared feature discovery, configuration and execution policy."""

from dataclasses import dataclass
from datetime import UTC, datetime

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import (
    DecisionReason,
    GroupAdminEvidence,
    PermissionRequest,
    PermissionTarget,
)
from hovorunv2.application.feature_policy import FeatureDecision, evaluate_feature
from hovorunv2.application.features import FeatureChannel, FeatureRegistry, FeatureScope, TriggerKind
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.groups.chat_status_service import ChatStatusService, evidence_time
from hovorunv2.application.identity.membership import MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.policy_facts import FeatureFactStore
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.domain.membership import GroupMembershipStateDB


@dataclass(frozen=True, slots=True)
class GroupSettingsStatus:
    """Operational guidance for a current manager, without internal restriction reasons."""

    access_reason: DecisionReason
    bot_verified: bool
    guidance: tuple[str, ...]


class FeatureAccessService:
    """Read current scope, access, selection and eligibility at every entry point."""

    def __init__(
        self,
        registry: FeatureRegistry,
        users: UserStore,
        chats: ChatStore,
        bans: BanStore,
        memberships: MembershipStore,
        permissions: PermissionService,
        chat_status: ChatStatusService,
        group_beta: GroupBetaStore,
        facts: FeatureFactStore,
    ) -> None:
        self.registry = registry
        self._users = users
        self._chats = chats
        self._bans = bans
        self._memberships = memberships
        self._permissions = permissions
        self._chat_status = chat_status
        self._group_beta = group_beta
        self._facts = facts

    def _admins_fresh(self, state: GroupMembershipStateDB | None, now: datetime) -> bool:
        """Require a complete, currently valid administrator snapshot."""
        if state is None or not state.admins_known:
            return False
        if state.admins_verified_at is None or state.admins_expires_at is None:
            return False
        return evidence_time(state.admins_verified_at) <= now < evidence_time(state.admins_expires_at)

    async def _manager(
        self, actor_id: int | None, target: PermissionTarget, now: datetime, *, inspection: bool = False
    ) -> bool:
        if actor_id is None:
            return False
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None or await self._bans.list_active(TargetKind.USER, actor_id):
            return False
        state = await self._memberships.get_state(target.entity_id or 0)
        member = await self._memberships.get_member(target.entity_id or 0, user.id)
        evidence = None
        if (
            (inspection or self._admins_fresh(state, now))
            and member is not None
            and member.status == MembershipStatus.ADMIN
        ):
            evidence = GroupAdminEvidence(
                actor_id, target.entity_id or 0, target.generation, member.verified_at, member.expires_at
            )
        return bool(
            await self._permissions.evaluate(
                PermissionRequest(actor_id, "group:settings", target, group_admin=evidence, transport_kind="group")
            )
        )

    async def evaluate(
        self,
        feature_key: str,
        chat_id: int,
        actor_id: int | None,
        *,
        trigger: TriggerKind = TriggerKind.DIRECT,
        configure: bool = False,
    ) -> FeatureDecision:
        """Evaluate fresh shared facts at discovery, configuration and execution boundaries."""
        definition = self.registry.get(feature_key)
        if definition is None:
            return FeatureDecision()
        facts = await self._facts.feature_facts(
            definition,
            chat_id,
            actor_id,
            trigger,
            configure=configure,
            now=datetime.now(UTC),
        )
        return evaluate_feature(definition, facts, trigger=trigger, configure=configure)

    async def group_settings_status(self, chat_id: int, actor_id: int) -> GroupSettingsStatus | None:
        """Explain current access and eligible capability requirements only to managers."""
        chat = await self._chats.get_chat(chat_id)
        if chat is None or chat.chat_id != chat_id or chat_id >= 0:
            return None
        now = datetime.now(UTC)
        target = PermissionTarget("group", chat.id, chat.generation, chat_id)
        manager = await self._manager(actor_id, target, now, inspection=True)
        inspector = not await self._bans.list_active(
            TargetKind.USER, actor_id
        ) and await self._permissions.has_authority(actor_id, "bot:administer")
        if not manager and not inspector:
            return None
        state = await self._memberships.get_state(chat.id)
        verified = bool(
            state is not None
            and state.bot_verified_at is not None
            and state.bot_expires_at is not None
            and evidence_time(state.bot_verified_at) <= now < evidence_time(state.bot_expires_at)
            and state.bot_status in {MembershipStatus.ADMIN, MembershipStatus.MEMBER}
        )
        guidance: set[str] = set()
        for definition in self.registry.all():
            decision = await self.evaluate(definition.key, chat_id, actor_id, configure=True)
            if decision.guidance:
                guidance.add(decision.guidance)
        access = await self._chat_status.evaluate_access(chat_id, now)
        return GroupSettingsStatus(access.reason, verified, tuple(sorted(guidance)))

    async def set_group_beta(
        self, key: str, chat_id: int, actor_id: int, *, enabled: bool, transport_kind: str = "group"
    ) -> bool:
        """Use the existing transactional authority and eligibility boundary for group selection."""
        definition = self.registry.require(key)
        chat = await self._chats.get_chat(chat_id)
        if chat is None or definition.channel != FeatureChannel.BETA or FeatureScope.GROUP not in definition.scopes:
            msg = "Group beta selection requires an active group and group-scoped beta feature."
            raise ValueError(msg)
        return await self._group_beta.set_enabled(
            PermissionTarget("group", chat.id, chat.generation, chat_id),
            key,
            enabled=enabled,
            actor=ActorContext.telegram(actor_id),
            transport_kind=transport_kind,
        )
