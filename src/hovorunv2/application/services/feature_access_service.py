"""Shared feature discovery, configuration and execution policy."""

from dataclasses import dataclass
from datetime import UTC, datetime

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.features import FeatureChannel, FeatureRegistry, FeatureScope, TriggerKind
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.membership import MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import GroupAdminEvidence, PermissionRequest, PermissionTarget
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.beta_service import BetaEligibilityService
from hovorunv2.application.services.chat_status_service import ChatStatusService, evidence_time
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.permission_service import PermissionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    """Safe discovery and execution result; guidance is restricted to managers."""

    visible: bool = False
    allowed: bool = False
    selected: bool = False
    guidance: str | None = None


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
        commands: CommandService,
        chat_status: ChatStatusService,
        beta: BetaEligibilityService,
        group_beta: GroupBetaStore,
    ) -> None:
        self.registry = registry
        self._users = users
        self._chats = chats
        self._bans = bans
        self._memberships = memberships
        self._permissions = permissions
        self._commands = commands
        self._chat_status = chat_status
        self._beta = beta
        self._group_beta = group_beta

    async def _manager(self, actor_id: int | None, target: PermissionTarget, now: datetime) -> bool:
        if actor_id is None:
            return False
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None or await self._bans.list_active(TargetKind.USER, actor_id):
            return False
        state = await self._memberships.get_state(target.entity_id or 0)
        member = await self._memberships.get_member(target.entity_id or 0, user.id)
        evidence = None
        if (
            state is not None
            and state.admins_known
            and state.admins_verified_at is not None
            and state.admins_expires_at is not None
            and evidence_time(state.admins_verified_at) <= now < evidence_time(state.admins_expires_at)
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

    async def _capable(self, target: PermissionTarget, required: frozenset[str], now: datetime) -> bool:
        if not required:
            return True
        state = await self._memberships.get_state(target.entity_id or 0)
        if (
            state is None
            or state.bot_verified_at is None
            or state.bot_expires_at is None
            or not evidence_time(state.bot_verified_at) <= now < evidence_time(state.bot_expires_at)
            or state.bot_status not in {MembershipStatus.ADMIN, MembershipStatus.MEMBER}
        ):
            return False
        rights = {
            "administrator": state.bot_status == MembershipStatus.ADMIN,
            "can_delete_messages": state.can_delete_messages,
            "can_restrict_members": state.can_restrict_members,
            "can_invite_users": state.can_invite_users,
            "can_pin_messages": state.can_pin_messages,
            "can_manage_chat": state.can_manage_chat,
        }
        return all(rights.get(right) is True for right in required)

    async def evaluate(
        self,
        feature_key: str,
        chat_id: int,
        actor_id: int | None,
        *,
        trigger: TriggerKind = TriggerKind.DIRECT,
        configure: bool = False,
    ) -> FeatureDecision:
        """Use configuration eligibility for toggles and selected availability for execution."""
        definition = self.registry.get(feature_key)
        if definition is None:
            return FeatureDecision()
        scope = FeatureScope.GROUP if chat_id < 0 else FeatureScope.PRIVATE
        if scope not in definition.scopes or (not configure and trigger not in definition.triggers):
            return FeatureDecision()
        automatic = scope == FeatureScope.GROUP and trigger == TriggerKind.AUTOMATIC and not configure
        if not automatic and (
            actor_id is None
            or await self._users.get_by_telegram_id(actor_id) is None
            or await self._bans.list_active(TargetKind.USER, actor_id)
        ):
            return FeatureDecision()
        if scope == FeatureScope.PRIVATE:
            return await self._private(feature_key, chat_id, actor_id, configure=configure)
        return await self._group(feature_key, chat_id, actor_id, configure=configure)

    async def _private(self, key: str, chat_id: int, actor_id: int | None, *, configure: bool) -> FeatureDecision:
        user = await self._users.get_by_telegram_id(chat_id)
        if user is None or actor_id != chat_id:
            return FeatureDecision()
        if not user.is_trusted and not await self._permissions.has_authority(chat_id, "bot:administer"):
            return FeatureDecision()
        definition = self.registry.require(key)
        target = PermissionTarget("user", user.id, user.generation, chat_id)
        if not await self._beta.is_eligible(target, key):
            return FeatureDecision()
        selected = await self._selected(key, chat_id)
        # Telegram group administrator capabilities do not exist in private transport.
        capable = not definition.required_bot_capabilities
        return FeatureDecision(
            visible=configure or (selected and capable), allowed=capable and (configure or selected), selected=selected
        )

    async def _selected(self, key: str, chat_id: int) -> bool:
        definition = self.registry.require(key)
        if (
            definition.channel == FeatureChannel.STANDARD
            and definition.provider is not None
            and definition.is_video is not None
        ):
            return await self._commands.is_capture_allowed(chat_id, definition.provider, is_video=definition.is_video)
        return await self._commands.is_command_allowed(chat_id, key)

    async def _group(self, key: str, chat_id: int, actor_id: int | None, *, configure: bool) -> FeatureDecision:
        chat = await self._chats.get_chat(chat_id)
        if chat is None:
            return FeatureDecision()
        now = datetime.now(UTC)
        target = PermissionTarget("group", chat.id, chat.generation, chat_id)
        manager = await self._manager(actor_id, target, now)
        if configure and not manager:
            return FeatureDecision()
        access = await self._chat_status.evaluate_access(chat_id, now)
        if not access.allowed:
            return FeatureDecision(
                guidance="Group access is unavailable. Check approval and administrator verification."
                if manager
                else None
            )
        definition = self.registry.require(key)
        if definition.channel == FeatureChannel.BETA:
            facts = await self._group_beta.facts(target, key, now)
            supported = facts.group_granted or facts.qualifying_enabler
            selected = facts.selected
            eligible = supported
            if configure and actor_id is not None:
                user = await self._users.get_by_telegram_id(actor_id)
                if user is not None:
                    eligible = eligible or await self._beta.is_eligible(
                        PermissionTarget("user", user.id, user.generation, actor_id), key
                    )
            if not eligible:
                return FeatureDecision()
        else:
            selected = await self._selected(key, chat_id)
            supported = True
        capable = await self._capable(target, definition.required_bot_capabilities, now)
        return FeatureDecision(
            visible=configure or (selected and supported and capable),
            allowed=capable and (configure or (selected and supported)),
            selected=selected,
            guidance="Bot permissions are missing or need verification." if manager and not capable else None,
        )

    async def set_group_beta(self, key: str, chat_id: int, actor_id: int, *, enabled: bool) -> bool:
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
            transport_kind="group",
        )
